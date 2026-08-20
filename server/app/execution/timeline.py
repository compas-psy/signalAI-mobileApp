"""Read-only forensic projection of durable execution facts (SAI-051)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    ExecutionFill,
    ExecutionIntent,
    ExecutionManagementPolicySnapshot,
    ExecutionManualTradeControl,
    ExecutionOrder,
    ExecutionProtection,
    ExecutionReconciliationEvent,
)


class ExecutionTimelineNotFound(LookupError):
    """The idea has no durable execution history."""


@dataclass(frozen=True)
class ExecutionTimelineEvent:
    source: str
    kind: str
    occurred_at: datetime
    facts: dict[str, Any]
    stable_id: str


@dataclass(frozen=True)
class ExecutionTimeline:
    idea_id: uuid.UUID
    intent_ids: tuple[uuid.UUID, ...]
    events: tuple[ExecutionTimelineEvent, ...]


def _enum_text(value: object) -> str:
    return str(getattr(value, "value", value))


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _uuid_text(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


def _event(
    *,
    source: str,
    kind: str,
    occurred_at: datetime,
    stable_id: object,
    facts: dict[str, Any],
) -> ExecutionTimelineEvent:
    return ExecutionTimelineEvent(
        source=source,
        kind=kind,
        occurred_at=occurred_at,
        stable_id=str(stable_id),
        facts=facts,
    )


_SOURCE_ORDER = {
    "intent": 0,
    "management_policy": 1,
    "order": 2,
    "fill": 3,
    "protection": 4,
    "reconciliation": 5,
    "manual_control": 6,
}


def read_execution_timeline(db: Session, *, idea_id: uuid.UUID) -> ExecutionTimeline:
    """Project one idea's execution history from durable facts only.

    The projection never manufactures state transitions. In particular, an
    order gets SUBMITTED/ACKNOWLEDGED events only when the corresponding
    persisted timestamps exist. Mutable ``updated_at`` fields are deliberately
    not treated as historical events.
    """

    intents = db.execute(
        select(ExecutionIntent)
        .where(ExecutionIntent.idea_id == idea_id)
        .order_by(ExecutionIntent.created_at, ExecutionIntent.id)
    ).scalars().all()
    if not intents:
        raise ExecutionTimelineNotFound(str(idea_id))

    intent_ids = tuple(intent.id for intent in intents)
    events: list[ExecutionTimelineEvent] = []

    for intent in intents:
        events.append(
            _event(
                source="intent",
                kind="INTENT_CREATED",
                occurred_at=intent.created_at,
                stable_id=intent.id,
                facts={
                    "intent_id": str(intent.id),
                    "state": _enum_text(intent.state),
                    "mode": _enum_text(intent.execution_mode_snapshot),
                    "venue": intent.venue,
                    "account": intent.account,
                    "instrument_id": intent.instrument_id,
                    "strategy_version": intent.strategy_version,
                    "risk_policy_snapshot_id": str(intent.risk_policy_snapshot_id),
                    "risk_override_id": _uuid_text(intent.risk_override_id),
                    "planned_quantity": _decimal_text(intent.planned_quantity),
                    "planned_entry_price": _decimal_text(intent.planned_entry_price),
                    "planned_stop_price": _decimal_text(intent.planned_stop_price),
                },
            )
        )

    snapshots = db.execute(
        select(ExecutionManagementPolicySnapshot)
        .where(ExecutionManagementPolicySnapshot.intent_id.in_(intent_ids))
        .order_by(
            ExecutionManagementPolicySnapshot.created_at,
            ExecutionManagementPolicySnapshot.id,
        )
    ).scalars().all()
    for snapshot in snapshots:
        events.append(
            _event(
                source="management_policy",
                kind="MANAGEMENT_POLICY_FROZEN",
                occurred_at=snapshot.created_at,
                stable_id=snapshot.id,
                facts={
                    "management_policy_snapshot_id": str(snapshot.id),
                    "intent_id": str(snapshot.intent_id),
                    "strategy_version": snapshot.strategy_version,
                    "risk_policy_snapshot_id": str(snapshot.risk_policy_snapshot_id),
                    "risk_override_id": _uuid_text(snapshot.risk_override_id),
                    "content_hash": snapshot.content_hash,
                },
            )
        )

    orders = db.execute(
        select(ExecutionOrder)
        .where(ExecutionOrder.intent_id.in_(intent_ids))
        .order_by(ExecutionOrder.created_at, ExecutionOrder.id)
    ).scalars().all()
    for order in orders:
        order_facts = {
            "order_id": str(order.id),
            "intent_id": str(order.intent_id),
            "provider_order_id": order.provider_order_id,
            "side": order.side,
            "order_type": order.order_type,
            "status": order.status,
            "quantity": _decimal_text(order.quantity),
            "limit_price": _decimal_text(order.limit_price),
            "stop_price": _decimal_text(order.stop_price),
        }
        events.append(
            _event(
                source="order",
                kind="ORDER_CREATED",
                occurred_at=order.created_at,
                stable_id=f"{order.id}:created",
                facts=order_facts,
            )
        )
        if order.submitted_at is not None:
            events.append(
                _event(
                    source="order",
                    kind="ORDER_SUBMITTED",
                    occurred_at=order.submitted_at,
                    stable_id=f"{order.id}:submitted",
                    facts=order_facts,
                )
            )
        if order.acknowledged_at is not None:
            events.append(
                _event(
                    source="order",
                    kind="ORDER_ACKNOWLEDGED",
                    occurred_at=order.acknowledged_at,
                    stable_id=f"{order.id}:acknowledged",
                    facts=order_facts,
                )
            )

    fills = db.execute(
        select(ExecutionFill)
        .where(ExecutionFill.intent_id.in_(intent_ids))
        .order_by(ExecutionFill.filled_at, ExecutionFill.id)
    ).scalars().all()
    for fill in fills:
        events.append(
            _event(
                source="fill",
                kind="FILL_RECORDED",
                occurred_at=fill.filled_at,
                stable_id=fill.id,
                facts={
                    "fill_id": str(fill.id),
                    "intent_id": str(fill.intent_id),
                    "order_id": str(fill.order_id),
                    "provider_fill_id": fill.provider_fill_id,
                    "quantity": _decimal_text(fill.quantity),
                    "price": _decimal_text(fill.price),
                    "fee_amount": _decimal_text(fill.fee_amount),
                    "fee_currency": fill.fee_currency,
                },
            )
        )

    protections = db.execute(
        select(ExecutionProtection)
        .where(ExecutionProtection.intent_id.in_(intent_ids))
        .order_by(ExecutionProtection.created_at, ExecutionProtection.id)
    ).scalars().all()
    for protection in protections:
        protection_facts = {
            "protection_id": str(protection.id),
            "intent_id": str(protection.intent_id),
            "order_id": _uuid_text(protection.order_id),
            "provider_order_id": protection.provider_order_id,
            "protection_type": protection.protection_type,
            "status": protection.status,
            "quantity": _decimal_text(protection.quantity),
            "stop_price": _decimal_text(protection.stop_price),
        }
        events.append(
            _event(
                source="protection",
                kind="PROTECTION_CREATED",
                occurred_at=protection.created_at,
                stable_id=f"{protection.id}:created",
                facts=protection_facts,
            )
        )
        if protection.armed_at is not None:
            events.append(
                _event(
                    source="protection",
                    kind="PROTECTION_ARMED",
                    occurred_at=protection.armed_at,
                    stable_id=f"{protection.id}:armed",
                    facts=protection_facts,
                )
            )
        if protection.last_reconciled_at is not None:
            events.append(
                _event(
                    source="protection",
                    kind="PROTECTION_RECONCILED",
                    occurred_at=protection.last_reconciled_at,
                    stable_id=f"{protection.id}:reconciled",
                    facts=protection_facts,
                )
            )

    reconciliations = db.execute(
        select(ExecutionReconciliationEvent)
        .where(ExecutionReconciliationEvent.intent_id.in_(intent_ids))
        .order_by(
            ExecutionReconciliationEvent.occurred_at,
            ExecutionReconciliationEvent.id,
        )
    ).scalars().all()
    for reconciliation in reconciliations:
        events.append(
            _event(
                source="reconciliation",
                kind=f"RECONCILIATION_{reconciliation.event_type}",
                occurred_at=reconciliation.occurred_at,
                stable_id=reconciliation.id,
                facts={
                    "reconciliation_event_id": str(reconciliation.id),
                    "intent_id": str(reconciliation.intent_id),
                    "event_type": reconciliation.event_type,
                    "outcome": reconciliation.outcome,
                    "detail": reconciliation.detail_json,
                },
            )
        )

    controls = db.execute(
        select(ExecutionManualTradeControl)
        .where(ExecutionManualTradeControl.intent_id.in_(intent_ids))
        .order_by(
            ExecutionManualTradeControl.created_at,
            ExecutionManualTradeControl.id,
        )
    ).scalars().all()
    for control in controls:
        events.append(
            _event(
                source="manual_control",
                kind=f"MANUAL_{control.action}_REQUESTED",
                occurred_at=control.created_at,
                stable_id=control.id,
                facts={
                    "command_id": str(control.id),
                    "intent_id": str(control.intent_id),
                    "management_policy_snapshot_id": str(
                        control.management_policy_snapshot_id
                    ),
                    "action": control.action,
                    "status": control.status,
                    "actor": control.actor,
                    "reason": control.reason,
                    "quantity": _decimal_text(control.requested_quantity),
                    "stop_price": _decimal_text(control.requested_stop),
                    "reduce_only": bool(control.reduce_only),
                    "order_id": _uuid_text(control.order_id),
                },
            )
        )

    events.sort(
        key=lambda event: (
            event.occurred_at,
            _SOURCE_ORDER[event.source],
            event.stable_id,
            event.kind,
        )
    )
    return ExecutionTimeline(
        idea_id=idea_id,
        intent_ids=intent_ids,
        events=tuple(events),
    )


__all__ = [
    "ExecutionTimeline",
    "ExecutionTimelineEvent",
    "ExecutionTimelineNotFound",
    "read_execution_timeline",
]
