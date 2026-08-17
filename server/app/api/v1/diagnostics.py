"""Aggregate and FORTS runtime diagnostics for the authenticated owner."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import (
    AuditEvent,
    DataQualityEvent,
    IdeaEvent,
    IdeaSkip,
    Instrument,
    NotificationOutbox,
    PaperTrade,
    TradeIdea,
)
from ...models.enums import AssetClass, IdeaStatus, PaperStatus, Venue
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


class FortsIdeaHealth(ApiModel):
    id: str
    status: str
    strategy: str
    signal_time: datetime
    expires_at: datetime


class FortsPaperHealth(ApiModel):
    id: str
    status: str
    lifecycle: str
    current_stop: str
    tps_taken: int
    remaining_fraction: float
    opened_at: datetime
    last_reconciled_at: datetime | None
    closed_at: datetime | None
    close_reason: str


class FortsRootHealth(ApiModel):
    root: str
    label: str
    symbol: str | None
    instrument_id: str | None
    monitored: bool
    admitted: bool
    stage: str
    primary_reason: str
    turnover_rub: str | None
    oi_notional_rub: str | None
    open_interest_contracts: str | None
    spread_pct: str | None
    closed_hourly_bars: int | None
    days_to_expiry: int | None
    snapshot_at: datetime | None
    updated_at: datetime | None
    idea: FortsIdeaHealth | None
    paper: FortsPaperHealth | None


class FortsRadarOut(ApiModel):
    request_id: str
    generated_at: datetime
    roots: list[FortsRootHealth]


_FORTS_CORE: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("SI", "USD/RUB", frozenset({"SI"})),
    ("CR", "CNY/RUB", frozenset({"CR"})),
    ("GOLD", "Золото", frozenset({"GD", "GL", "GOLD"})),
    ("SILV", "Серебро", frozenset({"SV", "S2", "SILV", "SILVER"})),
    ("BR", "Brent", frozenset({"BR", "BRENT"})),
    ("NG", "Газ", frozenset({"NG", "GAS"})),
)

_SETUP_STATUSES = frozenset(
    {
        IdeaStatus.WATCH,
        IdeaStatus.TRIGGERED,
        IdeaStatus.ACTIVE,
        IdeaStatus.PARTIALLY_FILLED,
        IdeaStatus.FILLED,
        IdeaStatus.TP1_HIT,
        IdeaStatus.MANAGING,
        IdeaStatus.TP2_HIT,
    }
)


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


def _meta_decimal(meta: dict, *keys: str) -> str | None:
    for key in keys:
        raw = meta.get(key)
        if raw in (None, ""):
            continue
        try:
            return str(Decimal(str(raw)))
        except (InvalidOperation, TypeError, ValueError):
            continue
    return None


def _meta_int(meta: dict, key: str) -> int | None:
    raw = meta.get(key)
    if raw in (None, ""):
        return None
    try:
        return int(Decimal(str(raw)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _meta_datetime(meta: dict, key: str) -> datetime | None:
    raw = meta.get(key)
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _family_root(instrument: Instrument) -> str:
    meta = instrument.metadata_json or {}
    return str(meta.get("root") or "").upper()


def _select_family_instrument(
    instruments: list[Instrument], aliases: frozenset[str], *, today: date
) -> Instrument | None:
    candidates = [item for item in instruments if _family_root(item) in aliases]
    if not candidates:
        return None

    def rank(item: Instrument) -> tuple[int, int, int, date, datetime]:
        expiry = item.expiry or date.min
        current = 1 if item.expiry is None or item.expiry >= today else 0
        return (
            current,
            1 if item.in_universe else 0,
            1 if item.is_tradable else 0,
            expiry,
            item.updated_at,
        )

    return max(candidates, key=rank)


def _primary_rejection(note: str) -> str:
    text = note.strip()
    if not text:
        return "не допущен: причина не сохранена"
    if ":" in text:
        text = text.split(":", 1)[1].strip()
    if ";" in text:
        text = text.split(";", 1)[0].strip()
    return text or "не допущен: причина не сохранена"


def _current_idea(db: Session, instrument_id: str, now: datetime) -> TradeIdea | None:
    idea = db.execute(
        select(TradeIdea)
        .where(TradeIdea.instrument_id == instrument_id)
        .order_by(TradeIdea.signal_time.desc())
        .limit(1)
    ).scalar_one_or_none()
    if idea is None or idea.expires_at < now or idea.status not in _SETUP_STATUSES:
        return None
    return idea


def _latest_paper(db: Session, instrument_id: str) -> PaperTrade | None:
    return db.execute(
        select(PaperTrade)
        .where(PaperTrade.instrument_id == instrument_id)
        .order_by(PaperTrade.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _paper_lifecycle(trade: PaperTrade) -> str:
    if trade.status is PaperStatus.PENDING:
        return "limit"
    if trade.status is PaperStatus.CANCELLED:
        return "cancelled"
    if trade.status is PaperStatus.CLOSED:
        return "closed"
    if trade.tps_taken <= 0:
        return "filled"
    if trade.tps_taken == 1:
        return "tp1"
    return "runner"


def _remaining_fraction(trade: PaperTrade) -> float:
    shares = trade.tp_shares or []
    taken = min(max(trade.tps_taken, 0), len(shares))
    used = Decimal(0)
    for raw in shares[:taken]:
        try:
            used += Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError):
            continue
    return float(max(Decimal(0), Decimal(1) - used))


def _forts_row(
    db: Session,
    canonical_root: str,
    label: str,
    aliases: frozenset[str],
    instruments: list[Instrument],
    now: datetime,
) -> FortsRootHealth:
    instrument = _select_family_instrument(instruments, aliases, today=now.date())
    if instrument is None:
        return FortsRootHealth(
            root=canonical_root,
            label=label,
            symbol=None,
            instrument_id=None,
            monitored=False,
            admitted=False,
            stage="not_observed",
            primary_reason="core-контракт не наблюдается",
            turnover_rub=None,
            oi_notional_rub=None,
            open_interest_contracts=None,
            spread_pct=None,
            closed_hourly_bars=None,
            days_to_expiry=None,
            snapshot_at=None,
            updated_at=None,
            idea=None,
            paper=None,
        )

    meta = instrument.metadata_json or {}
    admission = meta.get("admission") if isinstance(meta.get("admission"), dict) else {}
    idea = _current_idea(db, instrument.instrument_id, now)
    trade = _latest_paper(db, instrument.instrument_id)
    live_paper = trade is not None and trade.status in (PaperStatus.PENDING, PaperStatus.OPEN)

    if live_paper:
        stage = "paper_pending" if trade.status is PaperStatus.PENDING else "paper_open"
        primary_reason = f"PAPER · {_paper_lifecycle(trade)} · сопровождается сервером"
    elif not instrument.is_tradable:
        stage = "rejected"
        primary_reason = _primary_rejection(instrument.universe_note)
    elif idea is not None:
        stage = "setup"
        primary_reason = f"есть текущий сетап · {idea.status.value}"
    else:
        stage = "ready_no_setup"
        primary_reason = "допущен, текущего сетапа нет"

    days_to_expiry = _meta_int(admission, "days_to_expiry")
    if days_to_expiry is None and instrument.expiry is not None:
        days_to_expiry = (instrument.expiry - now.date()).days

    idea_out = None if idea is None else FortsIdeaHealth(
        id=str(idea.id),
        status=idea.status.value,
        strategy=idea.strategy.value,
        signal_time=idea.signal_time,
        expires_at=idea.expires_at,
    )
    paper_out = None if trade is None else FortsPaperHealth(
        id=str(trade.id),
        status=trade.status.value,
        lifecycle=_paper_lifecycle(trade),
        current_stop=str(trade.current_stop),
        tps_taken=trade.tps_taken,
        remaining_fraction=_remaining_fraction(trade),
        opened_at=trade.opened_at,
        last_reconciled_at=trade.last_reconciled_at,
        closed_at=trade.closed_at,
        close_reason=trade.close_reason,
    )

    return FortsRootHealth(
        root=canonical_root,
        label=label,
        symbol=instrument.symbol,
        instrument_id=instrument.instrument_id,
        monitored=True,
        admitted=instrument.is_tradable,
        stage=stage,
        primary_reason=primary_reason,
        turnover_rub=_meta_decimal(
            admission,
            "median_daily_notional_rub",
            "daily_notional_rub",
        ) or _meta_decimal(meta, "snapshot_turnover_rub"),
        oi_notional_rub=_meta_decimal(
            admission,
            "median_oi_notional_rub",
            "oi_notional_rub",
        ),
        open_interest_contracts=_meta_decimal(meta, "snapshot_open_interest"),
        spread_pct=_meta_decimal(admission, "relative_spread_snapshot")
        or _meta_decimal(meta, "spread_snapshot"),
        closed_hourly_bars=_meta_int(admission, "closed_hourly_bars"),
        days_to_expiry=days_to_expiry,
        snapshot_at=_meta_datetime(meta, "snapshot_at"),
        updated_at=instrument.updated_at,
        idea=idea_out,
        paper=paper_out,
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


@router.get("/forts-radar", response_model=FortsRadarOut)
def forts_radar(
    request: Request,
    db: Session = Depends(get_db),
) -> FortsRadarOut:
    """Read the six owner-facing FORTS families without running admission again."""
    now = datetime.now(UTC)
    instruments = list(
        db.execute(
            select(Instrument).where(
                Instrument.venue == Venue.MOEX,
                Instrument.asset_class == AssetClass.FUTURES,
            )
        ).scalars()
    )
    return FortsRadarOut(
        request_id=request.state.request_id,
        generated_at=now,
        roots=[
            _forts_row(db, root, label, aliases, instruments, now)
            for root, label, aliases in _FORTS_CORE
        ],
    )


__all__ = ["router"]
