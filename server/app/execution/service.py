"""Execution orchestration for SAI-026/027 and protection safety for SAI-039.

The service owns durable state progression and evidence persistence, while venue
I/O is injected through ``ExecutionPort``. Submit recovery is replay-safe and
protection is never considered active until the provider state is reconciled.
A naked position that cannot prove protection within the accepted 30-second SLA
moves to a durable emergency-flatten path with its own idempotent order.
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
_PROTECTION_SLA = timedelta(seconds=30)


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
class ProtectionReconciliation:
    outcome: str
    provider_order_id: str | None = None
    status: str | None = None
    quantity: Decimal | None = None
    stop_price: Decimal | None = None
    reconciled_at: datetime | None = None
    reason: str | None = None

    @classmethod
    def matched(
        cls,
        *,
        provider_order_id: str,
        status: str,
        quantity: Decimal,
        stop_price: Decimal,
        reconciled_at: datetime,
    ) -> "ProtectionReconciliation":
        return cls(
            outcome="MATCHED",
            provider_order_id=provider_order_id,
            status=status,
            quantity=Decimal(quantity),
            stop_price=Decimal(stop_price),
            reconciled_at=reconciled_at,
        )

    @classmethod
    def missing(cls, reason: str) -> "ProtectionReconciliation":
        return cls(outcome="MISSING", reason=reason)

    @classmethod
    def unknown(cls, reason: str) -> "ProtectionReconciliation":
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
    """Narrow execution I/O seam implemented by venue adapters."""

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

    def reconcile_protection(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
        protection: ExecutionProtection,
    ) -> ProtectionReconciliation: ...

    def emergency_flatten(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
        *,
        filled_quantity: Decimal,
        client_order_id: str,
    ) -> ExecutionSubmitAck: ...

    def reconcile_emergency_flatten(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
    ) -> SubmissionReconciliation: ...

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


def _emergency_client_order_id(intent: ExecutionIntent) -> str:
    return f"x-{intent.id.hex}"


def _entry_order(db: Session, intent: ExecutionIntent) -> ExecutionOrder | None:
    return db.execute(
        select(ExecutionOrder).where(
            ExecutionOrder.intent_id == intent.id,
            ExecutionOrder.client_order_id == _client_order_id(intent),
        )
    ).scalar_one_or_none()


def _emergency_order(db: Session, intent: ExecutionIntent) -> ExecutionOrder | None:
    return db.execute(
        select(ExecutionOrder).where(
            ExecutionOrder.intent_id == intent.id,
            ExecutionOrder.client_order_id == _emergency_client_order_id(intent),
        )
    ).scalar_one_or_none()


def _stop_protection(
    db: Session, intent: ExecutionIntent
) -> ExecutionProtection | None:
    return db.execute(
        select(ExecutionProtection)
        .where(
            ExecutionProtection.intent_id == intent.id,
            ExecutionProtection.protection_type == "STOP",
        )
        .order_by(ExecutionProtection.created_at.desc(), ExecutionProtection.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _filled_quantity(db: Session, intent: ExecutionIntent) -> Decimal:
    fills = db.execute(
        select(ExecutionFill).where(ExecutionFill.intent_id == intent.id)
    ).scalars()
    return sum((fill.quantity for fill in fills), start=Decimal("0"))


def _first_fill_at(db: Session, intent: ExecutionIntent) -> datetime | None:
    return db.execute(
        select(ExecutionFill.filled_at)
        .where(ExecutionFill.intent_id == intent.id)
        .order_by(ExecutionFill.filled_at.asc())
        .limit(1)
    ).scalar_one_or_none()


def _protection_sla_expired(
    db: Session,
    intent: ExecutionIntent,
    *,
    now: datetime,
) -> bool:
    exposed_at = _first_fill_at(db, intent)
    return exposed_at is not None and now - exposed_at >= _PROTECTION_SLA


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


def _ensure_protection_row(
    db: Session,
    *,
    intent: ExecutionIntent,
    order: ExecutionOrder,
    filled_quantity: Decimal,
) -> ExecutionProtection:
    protection = _stop_protection(db, intent)
    if protection is None:
        protection = ExecutionProtection(
            intent_id=intent.id,
            order_id=order.id,
            protection_type="STOP",
            status="PENDING",
            provider_order_id=None,
            quantity=filled_quantity,
            stop_price=intent.planned_stop_price,
            armed_at=None,
            last_reconciled_at=None,
        )
        db.add(protection)
    else:
        protection.order_id = order.id
        protection.quantity = filled_quantity
        protection.stop_price = intent.planned_stop_price
        if protection.provider_order_id is None:
            protection.status = "PENDING"
    db.flush()
    return protection


def _protection_match_is_exact(
    protection: ExecutionProtection,
    result: ProtectionReconciliation,
) -> bool:
    return (
        str(result.outcome).upper() == "MATCHED"
        and result.provider_order_id == protection.provider_order_id
        and result.quantity == protection.quantity
        and result.stop_price == protection.stop_price
        and result.reconciled_at is not None
    )


def _finish_protected(
    db: Session,
    *,
    intent: ExecutionIntent,
    protection: ExecutionProtection,
    result: ProtectionReconciliation,
    port: ExecutionPort,
) -> ExecutionProcessOutcome:
    protection.status = result.status or "ACTIVE"
    protection.last_reconciled_at = result.reconciled_at
    intent.next_retry_at = None
    _advance(intent, ExecutionState.PROTECTED)
    _record_reconciliation(
        db,
        intent,
        event_type="PROTECTION_RECONCILIATION",
        outcome="MATCHED",
        detail={
            "provider_order_id": protection.provider_order_id,
            "quantity": str(protection.quantity),
            "stop_price": str(protection.stop_price),
        },
    )
    db.flush()
    db.commit()

    port.reconcile(intent)
    _advance(intent, ExecutionState.MANAGING)
    port.manage_until_close(intent)
    return ExecutionProcessOutcome(True)


def _submit_emergency_flatten(
    db: Session,
    *,
    intent: ExecutionIntent,
    entry_order: ExecutionOrder,
    emergency_order: ExecutionOrder,
    filled_quantity: Decimal,
    port: ExecutionPort,
    now: datetime,
) -> ExecutionProcessOutcome:
    emergency_order.status = "SUBMITTING"
    emergency_order.submitted_at = now
    db.flush()
    db.commit()

    try:
        ack = port.emergency_flatten(
            intent,
            entry_order,
            filled_quantity=filled_quantity,
            client_order_id=emergency_order.client_order_id,
        )
    except (TimeoutError, ConnectionError) as exc:
        emergency_order.status = "AMBIGUOUS"
        _schedule_retry(intent, now)
        _record_reconciliation(
            db,
            intent,
            event_type="EMERGENCY_FLATTEN_SUBMIT",
            outcome="AMBIGUOUS",
            detail={
                "client_order_id": emergency_order.client_order_id,
                "reason": str(exc),
            },
        )
        db.commit()
        return ExecutionProcessOutcome(
            False, f"ambiguous emergency flatten result: {exc}"
        )
    except Exception as exc:
        emergency_order.status = "ERROR"
        _schedule_retry(intent, now)
        _record_reconciliation(
            db,
            intent,
            event_type="EMERGENCY_FLATTEN_SUBMIT",
            outcome="ERROR",
            detail={
                "client_order_id": emergency_order.client_order_id,
                "reason": str(exc),
            },
        )
        db.commit()
        return ExecutionProcessOutcome(False, f"emergency flatten failed: {exc}")

    emergency_order.provider_order_id = ack.provider_order_id
    emergency_order.status = ack.status
    emergency_order.acknowledged_at = ack.acknowledged_at
    _schedule_retry(intent, now)
    _record_reconciliation(
        db,
        intent,
        event_type="EMERGENCY_FLATTEN_SUBMIT",
        outcome="ACKNOWLEDGED",
        detail={
            "client_order_id": emergency_order.client_order_id,
            "provider_order_id": ack.provider_order_id,
        },
    )
    db.commit()
    return ExecutionProcessOutcome(False, "emergency flatten submitted; reconciliation pending")


def _begin_emergency_flatten(
    db: Session,
    *,
    intent: ExecutionIntent,
    entry_order: ExecutionOrder,
    port: ExecutionPort,
    now: datetime,
) -> ExecutionProcessOutcome:
    filled_quantity = _filled_quantity(db, intent)
    if filled_quantity <= 0:
        _schedule_retry(intent, now)
        db.commit()
        return ExecutionProcessOutcome(
            False, "emergency flatten requested without observed position quantity"
        )

    if intent.state != ExecutionState.EMERGENCY_FLATTEN:
        _advance(intent, ExecutionState.EMERGENCY_FLATTEN)

    emergency_order = _emergency_order(db, intent)
    if emergency_order is None:
        emergency_order = ExecutionOrder(
            intent_id=intent.id,
            client_order_id=_emergency_client_order_id(intent),
            provider_order_id=None,
            side="SELL" if entry_order.side == "BUY" else "BUY",
            order_type="EMERGENCY_FLATTEN",
            status="SUBMITTING",
            quantity=filled_quantity,
            limit_price=None,
            stop_price=None,
            submitted_at=now,
            acknowledged_at=None,
        )
        db.add(emergency_order)
        db.flush()
    else:
        emergency_order.quantity = filled_quantity

    return _submit_emergency_flatten(
        db,
        intent=intent,
        entry_order=entry_order,
        emergency_order=emergency_order,
        filled_quantity=filled_quantity,
        port=port,
        now=now,
    )


def _resume_emergency_flatten(
    db: Session,
    *,
    intent: ExecutionIntent,
    port: ExecutionPort,
    now: datetime,
) -> ExecutionProcessOutcome:
    entry_order = _entry_order(db, intent)
    if entry_order is None:
        _schedule_retry(intent, now)
        db.commit()
        return ExecutionProcessOutcome(False, "emergency flatten has no durable entry order")

    emergency_order = _emergency_order(db, intent)
    if emergency_order is None:
        return _begin_emergency_flatten(
            db,
            intent=intent,
            entry_order=entry_order,
            port=port,
            now=now,
        )

    try:
        result = port.reconcile_emergency_flatten(intent, emergency_order)
    except Exception as exc:
        result = SubmissionReconciliation.unknown(str(exc))

    outcome = str(result.outcome).upper()
    detail = {"client_order_id": emergency_order.client_order_id}
    if result.reason:
        detail["reason"] = result.reason
    _record_reconciliation(
        db,
        intent,
        event_type="EMERGENCY_FLATTEN_RECOVERY",
        outcome=outcome,
        detail=detail,
    )

    if outcome == "FOUND":
        if not result.provider_order_id or not result.status or result.acknowledged_at is None:
            _schedule_retry(intent, now)
            db.commit()
            return ExecutionProcessOutcome(
                False, "emergency flatten reconciliation lacks complete acknowledgement"
            )
        emergency_order.provider_order_id = result.provider_order_id
        emergency_order.status = result.status
        emergency_order.acknowledged_at = result.acknowledged_at
        if str(result.status).upper() == "FILLED":
            intent.next_retry_at = None
            _advance(intent, ExecutionState.CLOSED)
            db.commit()
            return ExecutionProcessOutcome(True)
        _schedule_retry(intent, now)
        db.commit()
        return ExecutionProcessOutcome(
            False, f"emergency flatten not closed yet: {result.status}"
        )

    if outcome == "ABSENT":
        emergency_order.provider_order_id = None
        emergency_order.acknowledged_at = None
        return _submit_emergency_flatten(
            db,
            intent=intent,
            entry_order=entry_order,
            emergency_order=emergency_order,
            filled_quantity=emergency_order.quantity,
            port=port,
            now=now,
        )

    _schedule_retry(intent, now)
    db.commit()
    return ExecutionProcessOutcome(
        False,
        result.reason or f"emergency flatten reconciliation outcome: {outcome}",
    )


def _resume_protection(
    db: Session,
    *,
    intent: ExecutionIntent,
    order: ExecutionOrder,
    protection: ExecutionProtection,
    port: ExecutionPort,
    now: datetime,
) -> ExecutionProcessOutcome:
    try:
        result = port.reconcile_protection(intent, order, protection)
    except Exception as exc:
        result = ProtectionReconciliation.unknown(str(exc))

    outcome = str(result.outcome).upper()
    if _protection_match_is_exact(protection, result):
        return _finish_protected(
            db,
            intent=intent,
            protection=protection,
            result=result,
            port=port,
        )

    reason = result.reason
    if outcome == "MATCHED":
        outcome = "MISMATCH"
        reason = "provider protection does not exactly match id, quantity and stop"

    protection.status = "UNCONFIRMED"
    if result.reconciled_at is not None:
        protection.last_reconciled_at = result.reconciled_at
    _record_reconciliation(
        db,
        intent,
        event_type="PROTECTION_RECONCILIATION",
        outcome=outcome,
        detail={
            "reason": reason or "",
            "provider_order_id": protection.provider_order_id,
            "quantity": str(protection.quantity),
            "stop_price": str(protection.stop_price),
        },
    )

    if _protection_sla_expired(db, intent, now=now):
        db.flush()
        return _begin_emergency_flatten(
            db,
            intent=intent,
            entry_order=order,
            port=port,
            now=now,
        )

    _schedule_retry(intent, now)
    db.commit()
    return ExecutionProcessOutcome(
        False, reason or f"protection reconciliation outcome: {outcome}"
    )


def _arm_or_resume_protection(
    db: Session,
    *,
    intent: ExecutionIntent,
    order: ExecutionOrder,
    protection: ExecutionProtection,
    filled_quantity: Decimal,
    port: ExecutionPort,
    now: datetime,
) -> ExecutionProcessOutcome:
    # If an ACK was ever persisted, never create a second stop blindly. Query
    # the provider first and let the SLA/emergency path handle uncertainty.
    if protection.provider_order_id:
        return _resume_protection(
            db,
            intent=intent,
            order=order,
            protection=protection,
            port=port,
            now=now,
        )

    try:
        ack = port.arm_protection(
            intent,
            order,
            filled_quantity=filled_quantity,
        )
    except (TimeoutError, ConnectionError) as exc:
        protection.status = "UNCONFIRMED"
        _record_reconciliation(
            db,
            intent,
            event_type="PROTECTION_ARM",
            outcome="AMBIGUOUS",
            detail={"reason": str(exc), "quantity": str(filled_quantity)},
        )
        if _protection_sla_expired(db, intent, now=now):
            db.flush()
            return _begin_emergency_flatten(
                db,
                intent=intent,
                entry_order=order,
                port=port,
                now=now,
            )
        _schedule_retry(intent, now)
        db.commit()
        return ExecutionProcessOutcome(False, f"ambiguous protection submit: {exc}")
    except Exception as exc:
        protection.status = "FAILED"
        _record_reconciliation(
            db,
            intent,
            event_type="PROTECTION_ARM",
            outcome="FAILED",
            detail={"reason": str(exc), "quantity": str(filled_quantity)},
        )
        db.flush()
        return _begin_emergency_flatten(
            db,
            intent=intent,
            entry_order=order,
            port=port,
            now=now,
        )

    protection.provider_order_id = ack.provider_order_id
    protection.status = "UNCONFIRMED"
    protection.armed_at = ack.armed_at
    _record_reconciliation(
        db,
        intent,
        event_type="PROTECTION_ARM",
        outcome="ACKNOWLEDGED",
        detail={
            "provider_order_id": ack.provider_order_id,
            "quantity": str(filled_quantity),
        },
    )
    # Persist the provider reference before the read-back query. A crash after
    # this commit resumes with reconciliation, never a second stop submission.
    db.flush()
    db.commit()

    return _resume_protection(
        db,
        intent=intent,
        order=order,
        protection=protection,
        port=port,
        now=now,
    )


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
    protection = _ensure_protection_row(
        db,
        intent=intent,
        order=order,
        filled_quantity=filled_quantity,
    )
    # Commit exposure + PROTECTION_PENDING before any protection provider I/O.
    db.commit()

    return _arm_or_resume_protection(
        db,
        intent=intent,
        order=order,
        protection=protection,
        filled_quantity=filled_quantity,
        port=port,
        now=now,
    )


def _recover_uncertain_submission(
    db: Session,
    *,
    intent: ExecutionIntent,
    order: ExecutionOrder,
    port: ExecutionPort,
    now: datetime,
) -> ExecutionProcessOutcome:
    if intent.state == ExecutionState.SUBMITTING:
        _advance(intent, ExecutionState.AMBIGUOUS)
    if intent.state == ExecutionState.AMBIGUOUS:
        _advance(intent, ExecutionState.RECONCILING)

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
    """Advance one durable intent without blind entry/protection/close replay."""

    now = now or datetime.now(UTC)
    intent = db.get(ExecutionIntent, intent_id)
    if intent is None:
        return ExecutionProcessOutcome(False, "execution intent not found")

    if not _retry_is_due(intent, now):
        return ExecutionProcessOutcome(False, "execution retry is not due yet")

    if intent.state == ExecutionState.EMERGENCY_FLATTEN:
        return _resume_emergency_flatten(
            db,
            intent=intent,
            port=port,
            now=now,
        )

    if intent.state == ExecutionState.PROTECTION_PENDING:
        order = _entry_order(db, intent)
        if order is None:
            return ExecutionProcessOutcome(
                False, "protection-pending execution has no durable entry order"
            )
        protection = _stop_protection(db, intent)
        if protection is None:
            filled_quantity = _filled_quantity(db, intent)
            if filled_quantity <= 0:
                return ExecutionProcessOutcome(
                    False, "protection-pending execution has no durable fills"
                )
            protection = _ensure_protection_row(
                db,
                intent=intent,
                order=order,
                filled_quantity=filled_quantity,
            )
            db.commit()
        return _arm_or_resume_protection(
            db,
            intent=intent,
            order=order,
            protection=protection,
            filled_quantity=protection.quantity,
            port=port,
            now=now,
        )

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
    "ProtectionReconciliation",
    "SubmissionReconciliation",
    "process_execution_intent",
]
