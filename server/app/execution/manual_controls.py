"""Monotonic owner controls for already-open trades (SAI-050 / B8.5)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.enums import Direction
from ..models.execution import (
    ExecutionFill,
    ExecutionIntent,
    ExecutionOrder,
    ExecutionProtection,
)
from ..models.ideas import TradeIdea
from ..models.management import ExecutionManagementPolicySnapshot
from ..models.manual_control import ExecutionManualTradeControl
from ..models.risk import AuditEvent
from .enums import ExecutionState


class ManualTradeAction(StrEnum):
    CLOSE = "CLOSE"
    REDUCE = "REDUCE"
    TIGHTEN_STOP = "TIGHTEN_STOP"
    RETURN_AUTO = "RETURN_AUTO"


class ManualTradeControlRejected(ValueError):
    """Owner request cannot be proven to reduce or preserve current risk."""


@dataclass(frozen=True)
class ManualTradeControlResult:
    command: ExecutionManualTradeControl
    order: ExecutionOrder | None
    created: bool


_PROVIDER_ACTIONS = frozenset(
    {
        ManualTradeAction.CLOSE,
        ManualTradeAction.REDUCE,
        ManualTradeAction.TIGHTEN_STOP,
    }
)
_PENDING_STATUSES = frozenset(
    {
        "REQUESTED",
        "SUBMITTING",
        "ACKNOWLEDGED",
        "RECONCILING",
        "PARTIALLY_FILLED",
    }
)
_REDUCING_ORDER_TYPES = frozenset(
    {
        "EMERGENCY_FLATTEN",
        "MANUAL_CLOSE",
        "MANUAL_REDUCE",
    }
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decimal_equal(left: Decimal | None, right: Decimal | None) -> bool:
    if left is None or right is None:
        return left is right
    return Decimal(left) == Decimal(right)


def _actual_exposure(db: Session, intent: ExecutionIntent) -> Decimal:
    """Net observed exposure from immutable fills, never from planned size."""

    entry = db.execute(
        select(func.coalesce(func.sum(ExecutionFill.quantity), 0))
        .join(ExecutionOrder, ExecutionFill.order_id == ExecutionOrder.id)
        .where(
            ExecutionFill.intent_id == intent.id,
            ExecutionOrder.order_type == "ENTRY",
        )
    ).scalar_one()
    reduced = db.execute(
        select(func.coalesce(func.sum(ExecutionFill.quantity), 0))
        .join(ExecutionOrder, ExecutionFill.order_id == ExecutionOrder.id)
        .where(
            ExecutionFill.intent_id == intent.id,
            ExecutionOrder.order_type.in_(_REDUCING_ORDER_TYPES),
        )
    ).scalar_one()
    exposure = Decimal(entry) - Decimal(reduced)
    return exposure if exposure > 0 else Decimal("0")


def _frozen_policy(
    db: Session,
    intent: ExecutionIntent,
) -> ExecutionManagementPolicySnapshot:
    snapshot = db.execute(
        select(ExecutionManagementPolicySnapshot).where(
            ExecutionManagementPolicySnapshot.intent_id == intent.id
        )
    ).scalar_one_or_none()
    if snapshot is None:
        raise ManualTradeControlRejected(
            "open trade has no frozen management policy snapshot"
        )
    return snapshot


def _current_stop(
    db: Session,
    *,
    intent: ExecutionIntent,
    snapshot: ExecutionManagementPolicySnapshot,
) -> Decimal:
    protection = db.execute(
        select(ExecutionProtection)
        .where(
            ExecutionProtection.intent_id == intent.id,
            ExecutionProtection.protection_type == "STOP",
        )
        .order_by(
            ExecutionProtection.last_reconciled_at.desc().nullslast(),
            ExecutionProtection.created_at.desc(),
            ExecutionProtection.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()
    if protection is not None and protection.stop_price is not None:
        return Decimal(protection.stop_price)

    raw = (snapshot.exit_profile_json or {}).get("initial_stop")
    if raw is None:
        raise ManualTradeControlRejected(
            "management policy does not contain a current stop reference"
        )
    try:
        value = Decimal(str(raw))
    except Exception as exc:
        raise ManualTradeControlRejected(
            "management policy contains an invalid stop reference"
        ) from exc
    if value <= 0:
        raise ManualTradeControlRejected(
            "management policy contains a non-positive stop reference"
        )
    return value


def _validate_payload(
    *,
    action: ManualTradeAction,
    exposure: Decimal,
    direction: Direction,
    current_stop: Decimal,
    requested_quantity: Decimal | None,
    requested_stop: Decimal | None,
) -> tuple[Decimal | None, Decimal | None]:
    quantity = Decimal(requested_quantity) if requested_quantity is not None else None
    stop = Decimal(requested_stop) if requested_stop is not None else None

    if action is ManualTradeAction.CLOSE:
        if quantity is not None or stop is not None:
            raise ManualTradeControlRejected(
                "CLOSE does not accept requested quantity or stop"
            )
        return None, None

    if action is ManualTradeAction.REDUCE:
        if stop is not None or quantity is None:
            raise ManualTradeControlRejected(
                "REDUCE requires quantity and does not accept stop"
            )
        if quantity <= 0 or quantity >= exposure:
            raise ManualTradeControlRejected(
                "REDUCE quantity must be positive and strictly below current exposure"
            )
        return quantity, None

    if action is ManualTradeAction.TIGHTEN_STOP:
        if quantity is not None or stop is None:
            raise ManualTradeControlRejected(
                "TIGHTEN_STOP requires stop and does not accept quantity"
            )
        if stop <= 0:
            raise ManualTradeControlRejected("TIGHTEN_STOP requires a positive stop")
        if direction is Direction.LONG:
            safer = stop > current_stop
        elif direction is Direction.SHORT:
            safer = stop < current_stop
        else:
            safer = False
        if not safer:
            raise ManualTradeControlRejected(
                "TIGHTEN_STOP must move only toward lower risk"
            )
        return None, stop

    if action is ManualTradeAction.RETURN_AUTO:
        if quantity is not None or stop is not None:
            raise ManualTradeControlRejected(
                "RETURN_AUTO does not accept requested quantity or stop"
            )
        return None, None

    raise ManualTradeControlRejected(f"unsupported manual action: {action}")


def _pending_provider_action(db: Session, intent_id: uuid.UUID) -> bool:
    command = db.execute(
        select(ExecutionManualTradeControl.id)
        .where(
            ExecutionManualTradeControl.intent_id == intent_id,
            ExecutionManualTradeControl.action.in_(
                tuple(item.value for item in _PROVIDER_ACTIONS)
            ),
            ExecutionManualTradeControl.status.in_(_PENDING_STATUSES),
        )
        .limit(1)
    ).scalar_one_or_none()
    return command is not None


def _client_order_id(action: ManualTradeAction, command_id: uuid.UUID) -> str:
    prefix = {
        ManualTradeAction.CLOSE: "mc",
        ManualTradeAction.REDUCE: "mr",
        ManualTradeAction.TIGHTEN_STOP: "ms",
    }[action]
    return f"{prefix}-{command_id.hex}"


def _replay(
    db: Session,
    *,
    existing: ExecutionManualTradeControl,
    action: ManualTradeAction,
    reason: str,
    requested_quantity: Decimal | None,
    requested_stop: Decimal | None,
) -> ManualTradeControlResult:
    if (
        existing.action != action.value
        or existing.reason != reason
        or not _decimal_equal(existing.requested_quantity, requested_quantity)
        or not _decimal_equal(existing.requested_stop, requested_stop)
    ):
        raise ManualTradeControlRejected(
            "idempotency key is already bound to a different manual trade control"
        )
    order = db.get(ExecutionOrder, existing.order_id) if existing.order_id else None
    return ManualTradeControlResult(command=existing, order=order, created=False)


def request_manual_trade_control(
    db: Session,
    *,
    intent_id: uuid.UUID,
    action: ManualTradeAction | str,
    idempotency_key: str,
    owner_reason: str,
    requested_quantity: Decimal | None,
    requested_stop: Decimal | None,
) -> ManualTradeControlResult:
    """Create one durable owner command without performing provider I/O.

    The request boundary is deliberately monotonic: CLOSE/REDUCE can only
    reduce observed exposure, TIGHTEN_STOP can only reduce stop risk, and
    RETURN_AUTO changes management ownership without creating an exchange
    order. Provider execution remains a later explicitly-capability-gated seam.
    """

    try:
        normalized_action = ManualTradeAction(action)
    except (TypeError, ValueError) as exc:
        raise ManualTradeControlRejected(f"unknown manual trade action: {action}") from exc

    key = str(idempotency_key or "").strip()
    reason = str(owner_reason or "").strip()
    if not key:
        raise ManualTradeControlRejected("idempotency key is required")
    if not reason:
        raise ManualTradeControlRejected("owner reason is required")
    key_digest = _digest(key)

    existing = db.execute(
        select(ExecutionManualTradeControl).where(
            ExecutionManualTradeControl.intent_id == intent_id,
            ExecutionManualTradeControl.idempotency_key_sha256 == key_digest,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _replay(
            db,
            existing=existing,
            action=normalized_action,
            reason=reason,
            requested_quantity=requested_quantity,
            requested_stop=requested_stop,
        )

    intent = db.get(ExecutionIntent, intent_id)
    if intent is None:
        raise ManualTradeControlRejected("execution intent does not exist")
    state = ExecutionState(intent.state)
    if state not in {ExecutionState.PROTECTED, ExecutionState.MANAGING}:
        raise ManualTradeControlRejected(
            f"manual trade control requires an open protected trade, got {state.value}"
        )

    snapshot = _frozen_policy(db, intent)
    idea = db.get(TradeIdea, intent.idea_id)
    if idea is None:
        raise ManualTradeControlRejected("execution idea does not exist")

    exposure = _actual_exposure(db, intent)
    if exposure <= 0:
        raise ManualTradeControlRejected("open trade has no observed filled exposure")
    current_stop = _current_stop(db, intent=intent, snapshot=snapshot)
    quantity, stop = _validate_payload(
        action=normalized_action,
        exposure=exposure,
        direction=idea.direction,
        current_stop=current_stop,
        requested_quantity=requested_quantity,
        requested_stop=requested_stop,
    )

    if _pending_provider_action(db, intent.id):
        raise ManualTradeControlRejected(
            "another provider-side manual trade control is still unresolved"
        )

    command = ExecutionManualTradeControl(
        intent_id=intent.id,
        management_policy_snapshot_id=snapshot.id,
        action=normalized_action.value,
        status=(
            "COMPLETED"
            if normalized_action is ManualTradeAction.RETURN_AUTO
            else "REQUESTED"
        ),
        idempotency_key_sha256=key_digest,
        actor="owner",
        reason=reason,
        requested_quantity=quantity,
        requested_stop=stop,
        reduce_only=True,
        order_id=None,
    )
    db.add(command)
    db.flush()

    order: ExecutionOrder | None = None
    if normalized_action in _PROVIDER_ACTIONS:
        order_quantity = exposure if quantity is None else quantity
        order = ExecutionOrder(
            intent_id=intent.id,
            client_order_id=_client_order_id(normalized_action, command.id),
            provider_order_id=None,
            side="SELL" if idea.direction is Direction.LONG else "BUY",
            order_type=f"MANUAL_{normalized_action.value}",
            status="REQUESTED",
            quantity=order_quantity,
            limit_price=None,
            stop_price=stop,
            submitted_at=None,
            acknowledged_at=None,
        )
        db.add(order)
        db.flush()
        command.order_id = order.id

    db.add(
        AuditEvent(
            actor="owner",
            action="manual_trade_control_requested",
            subject=str(intent.id),
            detail=reason,
            before_json={
                "state": state.value,
                "observed_exposure": str(exposure),
                "current_stop": str(current_stop),
                "management_policy_snapshot_id": str(snapshot.id),
            },
            after_json={
                "command_id": str(command.id),
                "action": normalized_action.value,
                "status": command.status,
                "order_id": str(order.id) if order is not None else None,
                "requested_quantity": str(quantity) if quantity is not None else None,
                "requested_stop": str(stop) if stop is not None else None,
                "reduce_only": True,
                "idempotency_key_sha256": key_digest,
            },
        )
    )
    db.flush()
    return ManualTradeControlResult(command=command, order=order, created=True)


__all__ = [
    "ManualTradeAction",
    "ManualTradeControlRejected",
    "ManualTradeControlResult",
    "request_manual_trade_control",
]
