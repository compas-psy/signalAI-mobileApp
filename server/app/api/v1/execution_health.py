"""Read-only execution health API for SAI-029."""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...db import get_db
from ...execution.health import (
    ExecutionHealthReport,
    execution_health_for_intent,
    execution_health_snapshot,
)
from ...schemas.common import ApiModel

router = APIRouter(tags=["execution-health"])


class ExecutionHealthViolationOut(ApiModel):
    code: str
    label: str
    detail: str


class ExecutionHealthItemOut(ApiModel):
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
    violations: list[ExecutionHealthViolationOut]


class ExecutionHealthAggregateOut(ApiModel):
    total_intents: int
    violation_intents: int
    protection_slo_breaches: int
    reconciliation_mismatches: int
    websocket_configured_intents: int
    websocket_stale_intents: int
    rejected_orders: int
    duplicate_preventions: int


class ExecutionHealthListOut(ApiModel):
    items: list[ExecutionHealthItemOut]
    aggregate: ExecutionHealthAggregateOut


def _item(report: ExecutionHealthReport) -> ExecutionHealthItemOut:
    return ExecutionHealthItemOut.model_validate(report)


@router.get("/execution/health", response_model=ExecutionHealthListOut)
def list_execution_health(
    limit: int = 20,
    db: Session = Depends(get_db),
) -> ExecutionHealthListOut:
    """Return recent execution health with violations attached to each trade."""

    reports, aggregate = execution_health_snapshot(
        db,
        limit=max(1, min(int(limit), 100)),
    )
    return ExecutionHealthListOut(
        items=[_item(report) for report in reports],
        aggregate=ExecutionHealthAggregateOut.model_validate(aggregate),
    )


@router.get(
    "/execution/health/{intent_id}",
    response_model=ExecutionHealthItemOut,
)
def execution_health_detail(
    intent_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> ExecutionHealthItemOut:
    try:
        report = execution_health_for_intent(db, intent_id=intent_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _item(report)


__all__ = [
    "ExecutionHealthAggregateOut",
    "ExecutionHealthItemOut",
    "ExecutionHealthListOut",
    "ExecutionHealthViolationOut",
    "execution_health_detail",
    "list_execution_health",
    "router",
]
