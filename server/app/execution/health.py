"""Per-trade execution health and SLO evidence (SAI-029 / R2).

All metrics are derived from durable execution evidence. The only new mutable
telemetry input is venue websocket health, which SAI-036 adapters may update
later. Missing venue telemetry is explicitly ``NOT_CONFIGURED`` and is never
reported as healthy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_config
from ..models.execution import (
    ExecutionFill,
    ExecutionIntent,
    ExecutionOrder,
    ExecutionProtection,
    ExecutionReconciliationEvent,
    ExecutionVenueHealth,
)
from ..models.ideas import TradeIdea


@dataclass(frozen=True)
class ExecutionHealthViolation:
    code: str
    label: str
    detail: str


@dataclass(frozen=True)
class ExecutionHealthReport:
    intent_id: uuid.UUID
    idea_id: uuid.UUID
    instrument_id: str
    venue: str
    account: str
    state: str
    decision_to_intent_ms: int | None
    submit_to_ack_ms: int | None
    fill_deviation_bps: Decimal | None
    protection_arm_ms: int | None
    protection_sla_ms: int
    reconciliation_mismatch_count: int
    websocket_state: str
    websocket_stale: bool | None
    rejected_order_count: int
    duplicate_prevention_count: int
    violations: tuple[ExecutionHealthViolation, ...]


@dataclass(frozen=True)
class ExecutionHealthSummary:
    total_intents: int
    violation_intents: int
    protection_slo_breaches: int
    reconciliation_mismatches: int
    websocket_configured_intents: int
    websocket_stale_intents: int
    rejected_orders: int
    duplicate_preventions: int


def _milliseconds(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return int(round((end - start).total_seconds() * 1000))


def _entry_order(orders: list[ExecutionOrder]) -> ExecutionOrder | None:
    candidates = [order for order in orders if order.submitted_at is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda item: item.submitted_at)


def _fill_deviation_bps(
    *,
    intent: ExecutionIntent,
    entry_order: ExecutionOrder | None,
    fills: list[ExecutionFill],
) -> Decimal | None:
    if not fills or intent.planned_entry_price <= 0:
        return None
    total_quantity = sum((fill.quantity for fill in fills), Decimal("0"))
    if total_quantity <= 0:
        return None
    weighted = sum(
        (fill.price * fill.quantity for fill in fills),
        Decimal("0"),
    ) / total_quantity
    planned = intent.planned_entry_price
    side = entry_order.side.upper() if entry_order is not None else "BUY"
    # Positive means worse execution relative to the approved plan. For SELL,
    # a lower fill is adverse; for BUY, a higher fill is adverse.
    adverse = (weighted - planned) if side == "BUY" else (planned - weighted)
    return (adverse / planned * Decimal("10000")).quantize(Decimal("0.01"))


def _reconciliation_mismatch(event: ExecutionReconciliationEvent) -> bool:
    expected = {
        "PRE_SUBMIT": {"ABSENT"},
        "SUBMISSION_RECOVERY": {"FOUND"},
        "POST_PROTECTION": {"MATCHED"},
    }
    outcome = event.outcome.upper()
    if event.event_type in expected:
        return outcome not in expected[event.event_type]
    return outcome in {
        "AMBIGUOUS",
        "MISMATCH",
        "NOT_FOUND",
        "REJECTED",
        "UNKNOWN",
    }


def _websocket_health(
    db: Session,
    *,
    intent: ExecutionIntent,
    as_of: datetime,
) -> tuple[str, bool | None]:
    venue_health = db.get(ExecutionVenueHealth, (intent.venue, intent.account))
    if venue_health is None:
        return "NOT_CONFIGURED", None
    if not venue_health.websocket_connected:
        return "DISCONNECTED", True
    if venue_health.last_websocket_message_at is None:
        return "NO_HEARTBEAT", True
    stale_after = timedelta(seconds=venue_health.stale_after_seconds)
    if as_of - venue_health.last_websocket_message_at > stale_after:
        return "STALE", True
    return "HEALTHY", False


def execution_health_for_intent(
    db: Session,
    *,
    intent_id: uuid.UUID,
    as_of: datetime | None = None,
) -> ExecutionHealthReport:
    """Build one owner-facing report from durable evidence for one intent."""

    as_of = as_of or datetime.now(UTC)
    intent = db.get(ExecutionIntent, intent_id)
    if intent is None:
        raise LookupError(f"execution intent {intent_id} does not exist")
    idea = db.get(TradeIdea, intent.idea_id)
    if idea is None:
        raise LookupError(f"execution idea {intent.idea_id} does not exist")

    orders = list(
        db.execute(
            select(ExecutionOrder)
            .where(ExecutionOrder.intent_id == intent.id)
            .order_by(ExecutionOrder.created_at, ExecutionOrder.id)
        ).scalars()
    )
    fills = list(
        db.execute(
            select(ExecutionFill)
            .where(ExecutionFill.intent_id == intent.id)
            .order_by(ExecutionFill.filled_at, ExecutionFill.id)
        ).scalars()
    )
    protections = list(
        db.execute(
            select(ExecutionProtection)
            .where(ExecutionProtection.intent_id == intent.id)
            .order_by(ExecutionProtection.created_at, ExecutionProtection.id)
        ).scalars()
    )
    reconciliation = list(
        db.execute(
            select(ExecutionReconciliationEvent)
            .where(ExecutionReconciliationEvent.intent_id == intent.id)
            .order_by(
                ExecutionReconciliationEvent.occurred_at,
                ExecutionReconciliationEvent.id,
            )
        ).scalars()
    )

    entry = _entry_order(orders)
    submit_to_ack_ms = _milliseconds(
        entry.submitted_at if entry else None,
        entry.acknowledged_at if entry else None,
    )
    decision_to_intent_ms = _milliseconds(idea.signal_time, intent.created_at)
    fill_deviation_bps = _fill_deviation_bps(
        intent=intent,
        entry_order=entry,
        fills=fills,
    )

    first_fill_at = min((fill.filled_at for fill in fills), default=None)
    first_armed_at = min(
        (item.armed_at for item in protections if item.armed_at is not None),
        default=None,
    )
    protection_arm_ms = _milliseconds(first_fill_at, first_armed_at)
    protection_sla_ms = int(get_config().get("execution.protection_sla_seconds")) * 1000

    mismatch_count = sum(1 for event in reconciliation if _reconciliation_mismatch(event))
    websocket_state, websocket_stale = _websocket_health(
        db,
        intent=intent,
        as_of=as_of,
    )
    rejected_order_count = sum(
        1 for order in orders if order.status.upper() == "REJECTED"
    )

    violations: list[ExecutionHealthViolation] = []
    if protection_arm_ms is not None and protection_arm_ms > protection_sla_ms:
        violations.append(
            ExecutionHealthViolation(
                code="PROTECTION_ARM_SLO",
                label="Защита поставлена позже SLA",
                detail=(
                    f"{protection_arm_ms} ms > {protection_sla_ms} ms from first fill"
                ),
            )
        )
    elif first_fill_at is not None and first_armed_at is None:
        naked_ms = _milliseconds(first_fill_at, as_of)
        if naked_ms is not None and naked_ms > protection_sla_ms:
            violations.append(
                ExecutionHealthViolation(
                    code="PROTECTION_ARM_SLO",
                    label="Защита не поставлена в SLA",
                    detail=f"no armed protection after {naked_ms} ms",
                )
            )
    if mismatch_count:
        violations.append(
            ExecutionHealthViolation(
                code="RECONCILIATION_MISMATCH",
                label="Сверка не совпала с ожидаемым состоянием",
                detail=f"{mismatch_count} reconciliation mismatch event(s)",
            )
        )
    if websocket_stale is True:
        violations.append(
            ExecutionHealthViolation(
                code="STALE_WEBSOCKET",
                label="Поток площадки устарел",
                detail=f"websocket state: {websocket_state}",
            )
        )
    if rejected_order_count:
        violations.append(
            ExecutionHealthViolation(
                code="REJECTED_ORDER",
                label="Площадка отклонила заявку",
                detail=f"{rejected_order_count} rejected order(s)",
            )
        )

    state = intent.state.value if hasattr(intent.state, "value") else str(intent.state)
    return ExecutionHealthReport(
        intent_id=intent.id,
        idea_id=intent.idea_id,
        instrument_id=intent.instrument_id,
        venue=intent.venue,
        account=intent.account,
        state=state,
        decision_to_intent_ms=decision_to_intent_ms,
        submit_to_ack_ms=submit_to_ack_ms,
        fill_deviation_bps=fill_deviation_bps,
        protection_arm_ms=protection_arm_ms,
        protection_sla_ms=protection_sla_ms,
        reconciliation_mismatch_count=mismatch_count,
        websocket_state=websocket_state,
        websocket_stale=websocket_stale,
        rejected_order_count=rejected_order_count,
        duplicate_prevention_count=int(intent.duplicate_prevention_count),
        violations=tuple(violations),
    )


def latest_execution_health_reports(
    db: Session,
    *,
    limit: int = 20,
    as_of: datetime | None = None,
) -> tuple[ExecutionHealthReport, ...]:
    if limit <= 0:
        return ()
    intents = list(
        db.execute(
            select(ExecutionIntent)
            .order_by(ExecutionIntent.created_at.desc(), ExecutionIntent.id.desc())
            .limit(min(limit, 100))
        ).scalars()
    )
    return tuple(
        execution_health_for_intent(db, intent_id=intent.id, as_of=as_of)
        for intent in intents
    )


def _summary(reports: tuple[ExecutionHealthReport, ...]) -> ExecutionHealthSummary:
    return ExecutionHealthSummary(
        total_intents=len(reports),
        violation_intents=sum(1 for item in reports if item.violations),
        protection_slo_breaches=sum(
            1
            for item in reports
            if any(v.code == "PROTECTION_ARM_SLO" for v in item.violations)
        ),
        reconciliation_mismatches=sum(
            item.reconciliation_mismatch_count for item in reports
        ),
        websocket_configured_intents=sum(
            1 for item in reports if item.websocket_state != "NOT_CONFIGURED"
        ),
        websocket_stale_intents=sum(1 for item in reports if item.websocket_stale is True),
        rejected_orders=sum(item.rejected_order_count for item in reports),
        duplicate_preventions=sum(item.duplicate_prevention_count for item in reports),
    )


def execution_health_summary(
    db: Session,
    *,
    limit: int = 20,
    as_of: datetime | None = None,
) -> ExecutionHealthSummary:
    return _summary(latest_execution_health_reports(db, limit=limit, as_of=as_of))


def execution_health_snapshot(
    db: Session,
    *,
    limit: int = 20,
    as_of: datetime | None = None,
) -> tuple[tuple[ExecutionHealthReport, ...], ExecutionHealthSummary]:
    reports = latest_execution_health_reports(db, limit=limit, as_of=as_of)
    return reports, _summary(reports)


__all__ = [
    "ExecutionHealthReport",
    "ExecutionHealthSummary",
    "ExecutionHealthViolation",
    "execution_health_for_intent",
    "execution_health_snapshot",
    "execution_health_summary",
    "latest_execution_health_reports",
]
