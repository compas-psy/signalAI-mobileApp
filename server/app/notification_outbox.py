"""Durable owner notification outbox.

The Android client may change networks, lose a VPN route, or be killed.  A
notification therefore cannot exist only in process memory.  The server writes
each semantic event under a stable dedup key and the phone consumes rows by a
monotonic cursor.  Reconnection is then lossless and idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import Instrument, PaperTrade, TradeIdea
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


def ensure_outbox(session: Session) -> None:
    # Kept outside Alembic deliberately: this is delivery infrastructure, not
    # trading truth.  Lazy creation also lets an existing VPS gain server push
    # without a destructive schema migration.
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS signalai_notification_outbox (
                id BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                dedup_key TEXT NOT NULL UNIQUE,
                kind VARCHAR(32) NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT ''
            )
            """
        )
    )


def emit(
    session: Session,
    *,
    key: str,
    kind: str,
    title: str,
    body: str,
    payload: str = "",
) -> int | None:
    ensure_outbox(session)
    return session.execute(
        text(
            """
            INSERT INTO signalai_notification_outbox
                (dedup_key, kind, title, body, payload)
            VALUES (:key, :kind, :title, :body, :payload)
            ON CONFLICT (dedup_key) DO NOTHING
            RETURNING id
            """
        ),
        {
            "key": key[:240],
            "kind": kind[:32],
            "title": title[:240],
            "body": body[:1000],
            "payload": payload[:500],
        },
    ).scalar_one_or_none()


def list_after(
    session: Session,
    after: int,
    *,
    limit: int = 100,
) -> list[NotificationEvent]:
    ensure_outbox(session)
    rows = session.execute(
        text(
            """
            SELECT id, created_at, dedup_key, kind, title, body, payload
            FROM signalai_notification_outbox
            WHERE id > :after
            ORDER BY id
            LIMIT :limit
            """
        ),
        {"after": max(0, int(after)), "limit": max(1, min(int(limit), 200))},
    ).all()
    return [
        NotificationEvent(
            id=int(row[0]),
            created_at=row[1],
            key=str(row[2]),
            kind=str(row[3]),
            title=str(row[4]),
            body=str(row[5]),
            payload=str(row[6] or ""),
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


__all__ = [
    "NotificationEvent",
    "emit",
    "ensure_outbox",
    "list_after",
    "materialize",
]
