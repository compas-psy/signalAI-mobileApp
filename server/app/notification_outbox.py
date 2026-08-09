"""Durable owner notification outbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import Instrument, NotificationOutbox, PaperTrade, TradeIdea
from .models.enums import IdeaStatus, PaperStatus, QualityStatus
from .operational_guard import reconcile_operational_lifecycle


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    id: int
    created_at: datetime
    key: str
    kind: str
    title: str
    body: str
    payload: str

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "key": self.key,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "payload": self.payload,
        }


def emit(
    session: Session,
    *,
    key: str,
    kind: str,
    title: str,
    body: str,
    payload: str = "",
) -> int | None:
    stmt = (
        insert(NotificationOutbox)
        .values(
            dedup_key=key[:240],
            kind=kind[:32],
            title=title[:240],
            body=body[:1000],
            payload=payload[:500],
        )
        .on_conflict_do_nothing(index_elements=[NotificationOutbox.dedup_key])
        .returning(NotificationOutbox.id)
    )
    return session.execute(stmt).scalar_one_or_none()


def list_after(
    session: Session,
    after: int,
    *,
    limit: int = 100,
) -> list[NotificationEvent]:
    rows = list(
        session.execute(
            select(NotificationOutbox)
            .where(NotificationOutbox.id > max(0, int(after)))
            .order_by(NotificationOutbox.id)
            .limit(max(1, min(int(limit), 200)))
        ).scalars()
    )
    return [
        NotificationEvent(
            id=row.id,
            created_at=row.created_at,
            key=row.dedup_key,
            kind=row.kind,
            title=row.title,
            body=row.body,
            payload=row.payload,
        )
        for row in rows
    ]


def _symbol(session: Session, instrument_id: str) -> str:
    value = session.execute(
        select(Instrument.symbol).where(Instrument.instrument_id == instrument_id)
    ).scalar_one_or_none()
    return str(value or instrument_id)


def _price(value: Decimal | float | int) -> str:
    raw = f"{Decimal(str(value)):.8f}".rstrip("0").rstrip(".")
    return raw.replace(".", ",")


def materialize(
    session: Session,
    *,
    now: datetime | None = None,
    include_smoke: bool = True,
) -> int:
    """Convert current server truth to durable, deduplicated notifications."""
    moment = now or datetime.now(UTC)
    reconcile_operational_lifecycle(session, now=moment)
    created = 0

    if include_smoke:
        created += int(
            emit(
                session,
                key="system:server-push-ready:v1",
                kind="SYSTEM",
                title="SignalAI · server push работает",
                body=(
                    "Тестовое уведомление создано на VPS и доставлено по "
                    "серверному каналу, без запуска проверки из приложения."
                ),
            )
            is not None
        )

    actionable = list(
        session.execute(
            select(TradeIdea).where(
                TradeIdea.status == IdeaStatus.TRIGGERED,
                TradeIdea.quality_status == QualityStatus.ACTIVE,
                TradeIdea.was_presented.is_(True),
                TradeIdea.expires_at > moment,
            )
        ).scalars()
    )
    for idea in actionable:
        symbol = _symbol(session, idea.instrument_id)
        created += int(
            emit(
                session,
                key=f"idea:{idea.id}:actionable",
                kind="IDEA",
                title=f"{symbol} · можно действовать",
                body=(
                    f"{str(idea.direction)} · вход {_price(idea.entry_low)}–"
                    f"{_price(idea.entry_high)} · стоп {_price(idea.stop)} · "
                    f"оценка {int(idea.score)}/100"
                ),
                payload=f"idea:{idea.id}",
            )
            is not None
        )

    live = list(
        session.execute(
            select(PaperTrade).where(
                PaperTrade.status.in_([PaperStatus.PENDING, PaperStatus.OPEN])
            )
        ).scalars()
    )
    for trade in live:
        symbol = _symbol(session, trade.instrument_id)
        status = PaperStatus(trade.status)
        if status is PaperStatus.PENDING:
            key = f"paper:{trade.id}:pending"
            title = f"{symbol} · paper принят"
            body = (
                f"Ждём вход {_price(trade.entry)}. План действителен до "
                f"{trade.expires_at.astimezone(UTC).strftime('%d.%m %H:%M')} UTC."
            )
        else:
            key = f"paper:{trade.id}:open:tp{trade.tps_taken}"
            title = f"{symbol} · paper в работе"
            body = (
                f"Вход исполнен · целей {trade.tps_taken}/{len(trade.tp_prices or [])} · "
                f"текущий стоп {_price(trade.current_stop)}."
            )
        created += int(
            emit(
                session,
                key=key,
                kind="PAPER",
                title=title,
                body=body,
                payload=f"idea:{trade.idea_id}",
            )
            is not None
        )

    recent_cutoff = moment - timedelta(hours=2)
    closed = list(
        session.execute(
            select(PaperTrade).where(
                PaperTrade.status.in_([PaperStatus.CLOSED, PaperStatus.CANCELLED]),
                PaperTrade.closed_at.is_not(None),
                PaperTrade.closed_at >= recent_cutoff,
            )
        ).scalars()
    )
    for trade in closed:
        symbol = _symbol(session, trade.instrument_id)
        reason = trade.close_reason or trade.outcome or "сделка завершена"
        created += int(
            emit(
                session,
                key=f"paper:{trade.id}:closed:{trade.outcome or trade.status}",
                kind="PAPER",
                title=f"{symbol} · paper завершён",
                body=reason,
                payload=f"idea:{trade.idea_id}",
            )
            is not None
        )

    return created


__all__ = ["NotificationEvent", "emit", "list_after", "materialize"]
