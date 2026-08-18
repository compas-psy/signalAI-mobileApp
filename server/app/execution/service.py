"""Execution orchestration for SAI-026 / B5.3 and SAI-027 / B5.4.

The service owns durable state progression and evidence persistence, but venue
I/O is injected through ``ExecutionPort``. SAI-027 makes submit recovery
replay-safe: the deterministic client order is persisted before network I/O and
any uncertain submit is reconciled before a later attempt may submit again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Protocol
from uuid import UUID

from sqlalchemy import select
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


_RETRY_BASE_SECONDS = 5
_RETRY_MAX_SECONDS = 300


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
class SubmissionReconciliation:
    outcome: str
    provider_order_id: str | None = None
    status: str | None = None
    acknowledged_at: datetime | None = None
    reason: str | None = None

    @classmethod
    def found(
        cls,
        *,
        provider_order_id: str,
        status: str,
        acknowledged_at: datetime,
    ) -> "SubmissionReconciliation":
        return cls(
            outcome="FOUND",
            provider_order_id=provider_order_id,
            status=status,
            acknowledged_at=acknowledged_at,
        )

    @classmethod
    def absent(cls) -> "SubmissionReconciliation":
        return cls(outcome="ABSENT")

    @classmethod
    def unknown(cls, reason: str) -> "SubmissionReconciliation":
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
    """Narrow execution I/O seam; SAI-036 will provide venue adapters."""

    def reconcile_before_submit(
        self, intent: ExecutionIntent
    ) -> PreSubmitReconciliation: ...

    def reconcile_submission(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
    ) -> SubmissionReconciliation: ...

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


def _schedule_retry(intent: ExecutionIntent, now: datetime) -> None:
    delay_seconds = min(
        _RETRY_MAX_SECONDS,
        _RETRY_BASE_SECONDS * (2 ** min(intent.retry_count, 6)),
    )
    intent.retry_count += 1
    intent.next_retry_at = now + timedelta(seconds=delay_seconds)


def _retry_is_due(intent: ExecutionIntent, now: datetime) -> bool:
    return intent.next_retry_at is None or intent.next_retry_at <= now


def _ensure_ready_to_submit(intent: ExecutionIntent) -> bool:
    if intent.state == ExecutionState.INTENT_CREATED:
        _advance(intent, ExecutionState.RISK_APPROVED)
    if intent.state == ExecutionState.RISK_APPROVED:
        _advance(intent, ExecutionState.READY_TO_SUBMIT)
    return intent.state == ExecutionState.READY_TO_SUBMIT


def _client_order_id(intent: ExecutionIntent) -> str:
    return f"e-{intent.id.hex}"


def _entry_order(db: Session, intent: ExecutionIntent) -> ExecutionOrder | None:
    return db.execute(
        select(ExecutionOrder).where(
            ExecutionOrder.intent_id == intent.id,
            ExecutionOrder.client_order_id == _client_order_id(intent),
        )
    ).scalar_one_or_none()


def _ensure_entry_order(
    db: Session,
    intent: ExecutionIntent,
    *,
    side: str,
    now: datetime,
) -> ExecutionOrder:
    order = _entry_order(db, intent)
    if order is None:
        order = ExecutionOrder(
            intent_id=intent.id,
            client_order_id=_client_order_id(intent),
            provider_order_id=None,
            side=side,
            order_type="ENTRY",
            status="SUBMITTING",
            quantity=intent.planned_quantity,
            limit_price=intent.planned_entry_price,
            stop_price=None,
            submitted_at=now,
            acknowledged_at=None,
        )
        db.add(order)
    else:
        order.status = "SUBMITTING"
        order.provider_order_id = None
        order.acknowledged_at = None
        order.submitted_at = now
    db.flush()
    return order


def _idea_side(db: Session, intent: ExecutionIntent) -> str | None:
    idea = db.get(TradeIdea, intent.idea_id)
    if idea is None:
        return None
    return "BUY" if idea.direction == Direction.LONG else "SELL"


def _continue_after_ack(
    db: Session,
    *,
    intent: ExecutionIntent,
    order: ExecutionOrder,
    port: ExecutionPort,
    now: datetime,
) -> ExecutionProcessOutcome:
    snapshots = list(port.consume_fills(intent, order))
    existing_fills = list(
        db.execute(
            select(ExecutionFill).where(ExecutionFill.order_id == order.id)
        ).scalars()
    )
    known_fill_ids = {fill.provider_fill_id for fill in existing_fills}

    for snapshot in snapshots:
        if snapshot.quantity <= 0 or snapshot.provider_fill_id in known_fill_ids:
            continue
        fill = ExecutionFill(
            intent_id=intent.id,
            order_id=order.id,
            provider_fill_id=snapshot.provider_fill_id,
            quantity=snapshot.quantity,
            price=snapshot.price,
            fee_amount=snapshot.fee_amount,
            fee_currency=snapshot.fee_currency,
            filled_at=snapshot.filled_at,
        )
        db.add(fill)
        existing_fills.append(fill)
        known_fill_ids.add(snapshot.provider_fill_id)

    db.flush()
    filled_quantity = sum(
        (fill.quantity for fill in existing_fills),
        start=Decimal("0"),
    )
    if filled_quantity <= 0:
        _schedule_retry(intent, now)
        db.commit()
        return ExecutionProcessOutcome(False, "entry acknowledged but no fills observed")

    intent.next_retry_at = None
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


def _recover_uncertain_submission(
    db: Session,
    *,
    intent: ExecutionIntent,
    order: ExecutionOrder,
    port: ExecutionPort,
    now: datetime,
) -> ExecutionProcessOutcome:
    # A restart may observe SUBMITTING even when the provider received the
    # request. Move through the explicit ambiguity states before any venue read.
    if intent.state == ExecutionState.SUBMITTING:
        _advance(intent, ExecutionState.AMBIGUOUS)
    if intent.state == ExecutionState.AMBIGUOUS:
        _advance(intent, ExecutionState.RECONCILING)

    # Persist RECONCILING before network I/O. If this process dies during the
    # lookup, the next worker still knows it must reconcile, not submit.
    db.flush()
    db.commit()

    result = port.reconcile_submission(intent, order)
    outcome = str(result.outcome).upper()
    detail = {"client_order_id": order.client_order_id}
    if getattr(result, "reason", None):
        detail["reason"] = result.reason
    _record_reconciliation(
        db,
        intent,
        event_type="SUBMISSION_RECOVERY",
        outcome=outcome,
        detail=detail,
    )

    if outcome == "FOUND":
        provider_order_id = getattr(result, "provider_order_id", None)
        status = getattr(result, "status", None)
        acknowledged_at = getattr(result, "acknowledged_at", None)
        if not provider_order_id or not status or acknowledged_at is None:
            _schedule_retry(intent, now)
            db.commit()
            return ExecutionProcessOutcome(
                False,
                "submission reconciliation FOUND without complete acknowledgement",
            )

        order.provider_order_id = provider_order_id
        order.status = status
        order.acknowledged_at = acknowledged_at
        intent.next_retry_at = None
        _advance(intent, ExecutionState.ACKNOWLEDGED)
        db.commit()
        return _continue_after_ack(
            db,
            intent=intent,
            order=order,
            port=port,
            now=now,
        )

    if outcome == "ABSENT":
        order.status = "NOT_FOUND"
        order.provider_order_id = None
        order.acknowledged_at = None
        _advance(intent, ExecutionState.READY_TO_SUBMIT)
        _schedule_retry(intent, now)
        db.commit()
        return ExecutionProcessOutcome(
            False,
            "authoritative reconciliation found no submitted entry; retry scheduled",
        )

    _schedule_retry(intent, now)
    db.commit()
    return ExecutionProcessOutcome(
        False,
        getattr(result, "reason", None)
        or f"submission reconciliation outcome: {outcome}",
    )


def process_execution_intent(
    db: Session,
    *,
    intent_id: UUID,
    port: ExecutionPort,
    now: datetime | None = None,
) -> ExecutionProcessOutcome:
    """Advance one durable intent without ever blindly replaying a submit.

    The stable client order row is committed before ``submit``. Any uncertain
    submit result becomes ``AMBIGUOUS`` and a later run must reconcile that row
    before an authoritative ABSENT may make the intent submit-eligible again.
    """

    now = now or datetime.now(UTC)
    intent = db.get(ExecutionIntent, intent_id)
    if intent is None:
        return ExecutionProcessOutcome(False, "execution intent not found")

    if not _retry_is_due(intent, now):
        return ExecutionProcessOutcome(False, "execution retry is not due yet")

    if intent.state in {
        ExecutionState.SUBMITTING,
        ExecutionState.AMBIGUOUS,
        ExecutionState.RECONCILING,
    }:
        order = _entry_order(db, intent)
        if order is None:
            side = _idea_side(db, intent)
            if side is None:
                return ExecutionProcessOutcome(False, "execution idea disappeared")
            order = _ensure_entry_order(db, intent, side=side, now=now)
        return _recover_uncertain_submission(
            db,
            intent=intent,
            order=order,
            port=port,
            now=now,
        )

    if intent.state == ExecutionState.ACKNOWLEDGED:
        order = _entry_order(db, intent)
        if order is None:
            return ExecutionProcessOutcome(
                False, "acknowledged execution has no durable entry order"
            )
        return _continue_after_ack(
            db,
            intent=intent,
            order=order,
            port=port,
            now=now,
        )

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
        _schedule_retry(intent, now)
        db.commit()
        return ExecutionProcessOutcome(
            False,
            pre.reason or f"pre-submit reconciliation outcome: {pre.outcome}",
        )

    side = _idea_side(db, intent)
    if side is None:
        return ExecutionProcessOutcome(False, "execution idea disappeared")

    order = _ensure_entry_order(db, intent, side=side, now=now)
    _advance(intent, ExecutionState.SUBMITTING)

    # This commit is the core crash-safety boundary: after it succeeds there is
    # always one durable stable client id to reconcile if the network outcome
    # is lost.
    db.commit()

    try:
        ack = port.submit(intent, client_order_id=order.client_order_id)
    except (TimeoutError, ConnectionError) as exc:
        _advance(intent, ExecutionState.AMBIGUOUS)
        _schedule_retry(intent, now)
        _record_reconciliation(
            db,
            intent,
            event_type="SUBMIT_RESULT",
            outcome="AMBIGUOUS",
            detail={
                "client_order_id": order.client_order_id,
                "reason": str(exc),
            },
        )
        db.commit()
        return ExecutionProcessOutcome(
            False,
            f"ambiguous submit result: {exc}",
        )

    order.provider_order_id = ack.provider_order_id
    order.status = ack.status
    order.acknowledged_at = ack.acknowledged_at
    intent.next_retry_at = None
    _advance(intent, ExecutionState.ACKNOWLEDGED)
    db.commit()

    return _continue_after_ack(
        db,
        intent=intent,
        order=order,
        port=port,
        now=now,
    )


__all__ = [
    "ExecutionFillSnapshot",
    "ExecutionPort",
    "ExecutionProcessOutcome",
    "ExecutionProtectionAck",
    "ExecutionSubmitAck",
    "PreSubmitReconciliation",
    "SubmissionReconciliation",
    "process_execution_intent",
]
