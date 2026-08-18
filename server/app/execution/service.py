"""Execution orchestration for SAI-026 / B5.3.

The service owns durable state progression and evidence persistence, but venue
I/O is injected through ``ExecutionPort``. The production worker intentionally
uses a disabled port until SAI-036 provides a real VenueAdapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from ..models.enums import Direction
from ..models.execution import (
    ExecutionFill,
    ExecutionIntent,
    ExecutionOrder,
    ExecutionProtection,
    ExecutionReconciliationEvent,
)
from ..models.ideas import TradeIdea
from .domain import transition_execution_state
from .enums import ExecutionState


@dataclass(frozen=True)
class PreSubmitReconciliation:
    outcome: str
    reason: str | None = None

    @classmethod
    def absent(cls) -> "PreSubmitReconciliation":
        return cls(outcome="ABSENT")

    @classmethod
    def unknown(cls, reason: str) -> "PreSubmitReconciliation":
        return cls(outcome="UNKNOWN", reason=reason)


@dataclass(frozen=True)
class ExecutionSubmitAck:
    provider_order_id: str
    status: str
    acknowledged_at: datetime


@dataclass(frozen=True)
class ExecutionFillSnapshot:
    provider_fill_id: str
    quantity: Decimal
    price: Decimal
    fee_amount: Decimal
    fee_currency: str | None
    filled_at: datetime


@dataclass(frozen=True)
class ExecutionProtectionAck:
    provider_order_id: str
    status: str
    armed_at: datetime


@dataclass(frozen=True)
class ExecutionProcessOutcome:
    processed: bool
    blocked_reason: str | None = None


class ExecutionPort(Protocol):
    """Narrow SAI-026 I/O seam; SAI-036 will provide venue adapters."""

    def reconcile_before_submit(
        self, intent: ExecutionIntent
    ) -> PreSubmitReconciliation: ...

    def submit(
        self, intent: ExecutionIntent, *, client_order_id: str
    ) -> ExecutionSubmitAck: ...

    def consume_fills(
        self, intent: ExecutionIntent, order: ExecutionOrder
    ) -> Iterable[ExecutionFillSnapshot]: ...

    def arm_protection(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
        *,
        filled_quantity: Decimal,
    ) -> ExecutionProtectionAck: ...

    def reconcile(self, intent: ExecutionIntent) -> None: ...

    def manage_until_close(self, intent: ExecutionIntent) -> None: ...


def _advance(intent: ExecutionIntent, target: ExecutionState) -> None:
    intent.state = transition_execution_state(intent.state, target)


def _record_reconciliation(
    db: Session,
    intent: ExecutionIntent,
    *,
    event_type: str,
    outcome: str,
    detail: dict | None = None,
) -> None:
    db.add(
        ExecutionReconciliationEvent(
            intent_id=intent.id,
            event_type=event_type,
            outcome=outcome,
            detail_json=detail or {},
        )
    )


def _ensure_ready_to_submit(intent: ExecutionIntent) -> bool:
    if intent.state == ExecutionState.INTENT_CREATED:
        _advance(intent, ExecutionState.RISK_APPROVED)
    if intent.state == ExecutionState.RISK_APPROVED:
        _advance(intent, ExecutionState.READY_TO_SUBMIT)
    return intent.state == ExecutionState.READY_TO_SUBMIT


def process_execution_intent(
    db: Session,
    *,
    intent_id: UUID,
    port: ExecutionPort,
) -> ExecutionProcessOutcome:
    """Advance one durable intent through the B5.3 happy path.

    Retry ambiguity and re-submit policy are deliberately left to SAI-027.
    Therefore this slice submits only from ``READY_TO_SUBMIT`` and an UNKNOWN
    pre-submit reconciliation leaves the intent READY without any order row.
    """

    intent = db.get(ExecutionIntent, intent_id)
    if intent is None:
        return ExecutionProcessOutcome(False, "execution intent not found")
    if not _ensure_ready_to_submit(intent):
        return ExecutionProcessOutcome(
            False, f"execution state is not claimable: {intent.state.value}"
        )

    pre = port.reconcile_before_submit(intent)
    _record_reconciliation(
        db,
        intent,
        event_type="PRE_SUBMIT",
        outcome=pre.outcome,
        detail={"reason": pre.reason} if pre.reason else {},
    )
    if pre.outcome != "ABSENT":
        return ExecutionProcessOutcome(
            False,
            pre.reason or f"pre-submit reconciliation outcome: {pre.outcome}",
        )

    idea = db.get(TradeIdea, intent.idea_id)
    if idea is None:
        return ExecutionProcessOutcome(False, "execution idea disappeared")
    side = "BUY" if idea.direction == Direction.LONG else "SELL"

    client_order_id = f"e-{intent.id.hex}"
    _advance(intent, ExecutionState.SUBMITTING)
    ack = port.submit(intent, client_order_id=client_order_id)
    order = ExecutionOrder(
        intent_id=intent.id,
        client_order_id=client_order_id,
        provider_order_id=ack.provider_order_id,
        side=side,
        order_type="ENTRY",
        status=ack.status,
        quantity=intent.planned_quantity,
        limit_price=intent.planned_entry_price,
        stop_price=None,
        submitted_at=ack.acknowledged_at,
        acknowledged_at=ack.acknowledged_at,
    )
    db.add(order)
    db.flush()
    _advance(intent, ExecutionState.ACKNOWLEDGED)

    snapshots = list(port.consume_fills(intent, order))
    filled_quantity = Decimal("0")
    for snapshot in snapshots:
        if snapshot.quantity <= 0:
            continue
        db.add(
            ExecutionFill(
                intent_id=intent.id,
                order_id=order.id,
                provider_fill_id=snapshot.provider_fill_id,
                quantity=snapshot.quantity,
                price=snapshot.price,
                fee_amount=snapshot.fee_amount,
                fee_currency=snapshot.fee_currency,
                filled_at=snapshot.filled_at,
            )
        )
        filled_quantity += snapshot.quantity

    if filled_quantity <= 0:
        return ExecutionProcessOutcome(False, "entry acknowledged but no fills observed")
    if filled_quantity < intent.planned_quantity:
        _advance(intent, ExecutionState.PARTIALLY_FILLED)
    else:
        _advance(intent, ExecutionState.FILLED)

    _advance(intent, ExecutionState.PROTECTION_PENDING)
    protection_ack = port.arm_protection(
        intent,
        order,
        filled_quantity=filled_quantity,
    )
    protection = ExecutionProtection(
        intent_id=intent.id,
        order_id=order.id,
        protection_type="STOP",
        status=protection_ack.status,
        provider_order_id=protection_ack.provider_order_id,
        quantity=filled_quantity,
        stop_price=intent.planned_stop_price,
        armed_at=protection_ack.armed_at,
        last_reconciled_at=None,
    )
    db.add(protection)
    db.flush()
    _advance(intent, ExecutionState.PROTECTED)

    port.reconcile(intent)
    protection.last_reconciled_at = protection_ack.armed_at
    _record_reconciliation(
        db,
        intent,
        event_type="POST_PROTECTION",
        outcome="MATCHED",
    )

    _advance(intent, ExecutionState.MANAGING)
    port.manage_until_close(intent)
    return ExecutionProcessOutcome(True)


__all__ = [
    "ExecutionFillSnapshot",
    "ExecutionPort",
    "ExecutionProcessOutcome",
    "ExecutionProtectionAck",
    "ExecutionSubmitAck",
    "PreSubmitReconciliation",
    "process_execution_intent",
]
