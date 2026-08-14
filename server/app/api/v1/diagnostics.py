"""Aggregate-only runtime diagnostics for the authenticated owner."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import (
    AuditEvent,
    DataQualityEvent,
    IdeaEvent,
    IdeaSkip,
    NotificationOutbox,
    PaperTrade,
    TradeIdea,
)
from ...models.enums import PaperStatus
from ...schemas.common import ApiModel

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


class IdeasHealth(ApiModel):
    total: int
    by_status: dict[str, int]
    latest_signal_at: datetime | None


class PaperHealth(ApiModel):
    total: int
    by_status: dict[str, int]
    live: int
    unreconciled_live: int
    oldest_live_reconciled_at: datetime | None


class NotificationsHealth(ApiModel):
    total: int
    latest_id: int | None
    latest_created_at: datetime | None


class DecisionsHealth(ApiModel):
    approved: int
    rejected: int


class LifecycleHealth(ApiModel):
    total: int
    by_status: dict[str, int]
    latest_event_at: datetime | None


class DataQualityHealth(ApiModel):
    total: int
    by_flag: dict[str, int]
    latest_event_at: datetime | None


class IdempotencyHealth(ApiModel):
    approve_replays: int
    reject_replays: int


class RuntimeDiagnosticsOut(ApiModel):
    request_id: str
    generated_at: datetime
    ideas: IdeasHealth
    paper: PaperHealth
    notifications: NotificationsHealth
    decisions: DecisionsHealth
    lifecycle: LifecycleHealth
    data_quality: DataQualityHealth
    idempotency: IdempotencyHealth


def _value_counts(db: Session, model, value_column) -> dict[str, int]:
    rows = db.execute(
        select(value_column, func.count(model.id)).group_by(value_column)
    ).all()
    return {
        str(getattr(value, "value", value)): int(count)
        for value, count in rows
    }


def _audit_action_count(db: Session, action: str) -> int:
    return int(
        db.scalar(
            select(func.count(AuditEvent.id)).where(AuditEvent.action == action)
        )
        or 0
    )


@router.get("/runtime", response_model=RuntimeDiagnosticsOut)
def runtime_diagnostics(
    request: Request,
    db: Session = Depends(get_db),
) -> RuntimeDiagnosticsOut:
    idea_statuses = _value_counts(db, TradeIdea, TradeIdea.status)
    latest_signal_at = db.scalar(select(func.max(TradeIdea.signal_time)))

    paper_statuses = _value_counts(db, PaperTrade, PaperTrade.status)
    live_statuses = (PaperStatus.PENDING, PaperStatus.OPEN)
    live = int(
        db.scalar(
            select(func.count(PaperTrade.id)).where(
                PaperTrade.status.in_(live_statuses)
            )
        )
        or 0
    )
    unreconciled_live = int(
        db.scalar(
            select(func.count(PaperTrade.id)).where(
                PaperTrade.status.in_(live_statuses),
                PaperTrade.last_reconciled_at.is_(None),
            )
        )
        or 0
    )
    oldest_live_reconciled_at = db.scalar(
        select(func.min(PaperTrade.last_reconciled_at)).where(
            PaperTrade.status.in_(live_statuses),
            PaperTrade.last_reconciled_at.is_not(None),
        )
    )

    notification_total = int(
        db.scalar(select(func.count(NotificationOutbox.id))) or 0
    )
    latest_notification = db.execute(
        select(NotificationOutbox.id, NotificationOutbox.created_at)
        .order_by(NotificationOutbox.id.desc())
        .limit(1)
    ).first()

    rejected_decisions = int(
        db.scalar(select(func.count(IdeaSkip.idea_id))) or 0
    )

    lifecycle_statuses = _value_counts(db, IdeaEvent, IdeaEvent.new_status)
    latest_lifecycle_event = db.scalar(select(func.max(IdeaEvent.occurred_at)))

    quality_flags = _value_counts(db, DataQualityEvent, DataQualityEvent.flag)
    latest_quality_event = db.scalar(select(func.max(DataQualityEvent.occurred_at)))

    return RuntimeDiagnosticsOut(
        request_id=request.state.request_id,
        generated_at=datetime.now(UTC),
        ideas=IdeasHealth(
            total=sum(idea_statuses.values()),
            by_status=idea_statuses,
            latest_signal_at=latest_signal_at,
        ),
        paper=PaperHealth(
            total=sum(paper_statuses.values()),
            by_status=paper_statuses,
            live=live,
            unreconciled_live=unreconciled_live,
            oldest_live_reconciled_at=oldest_live_reconciled_at,
        ),
        notifications=NotificationsHealth(
            total=notification_total,
            latest_id=None if latest_notification is None else latest_notification.id,
            latest_created_at=(
                None if latest_notification is None else latest_notification.created_at
            ),
        ),
        decisions=DecisionsHealth(
            approved=sum(paper_statuses.values()),
            rejected=rejected_decisions,
        ),
        lifecycle=LifecycleHealth(
            total=sum(lifecycle_statuses.values()),
            by_status=lifecycle_statuses,
            latest_event_at=latest_lifecycle_event,
        ),
        data_quality=DataQualityHealth(
            total=sum(quality_flags.values()),
            by_flag=quality_flags,
            latest_event_at=latest_quality_event,
        ),
        idempotency=IdempotencyHealth(
            approve_replays=_audit_action_count(db, "approve_paper_replay"),
            reject_replays=_audit_action_count(db, "reject_replay"),
        ),
    )


__all__ = ["router"]
