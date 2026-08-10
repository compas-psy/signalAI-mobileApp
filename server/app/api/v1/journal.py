"""Серверный журнал владельца.

Thin-клиент не должен собирать историю из локальных файлов: источник истины —
PostgreSQL на VPS. Paper-сделки читаются отдельным endpoint `/paper/trades`.
Здесь остаются два класса решений, которые не должны исчезать бесследно:

* явные отказы владельца;
* показанные идеи, завершившиеся до создания paper-сделки.

Второй класс критичен для WATCH/TRIGGERED FORTS: супервизор честно пишет
WATCH → CANCELLED/MISSED/TIMED_OUT в ``idea_events``, но раньше thin-журнал
читал только paper ledger и ``idea_skips``. В результате карточка исчезала из
«Наблюдения», хотя полная история оставалась в PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import IdeaSkip, PaperTrade, TradeIdea
from ...models.enums import IdeaStatus
from ...schemas.common import ApiModel, Money

router = APIRouter(prefix="/journal", tags=["journal"])


class SkipOut(ApiModel):
    idea_id: UUID
    instrument_id: str
    symbol: str
    direction: str
    strategy: str
    score: Money
    reason: str
    comment: str = ""
    skipped_at: datetime


class MissOut(SkipOut):
    """Показанная идея, которая закончилась до создания сделки."""

    status: str


def _value(raw: object) -> str:
    """API отдаёт значение enum, а не repr Python-класса."""
    return str(getattr(raw, "value", raw))


_REASON_LABELS = {
    "stopped_after_entry": "Замысел отменён: после входной зоны цена прошла стоп",
    "invalidated_before_entry": "Замысел отменён до входа",
    "played_out_without_us": "Идея отработала без подтверждённой сделки",
    "price_left_without_entry": "Движение прошло без входа",
    "watch_ttl_expired": "Не дождались подтверждения в срок",
    "ttl_expired": "Истёк срок идеи",
}


def _miss_reason(status: IdeaStatus, reason_code: str) -> str:
    return _REASON_LABELS.get(reason_code, f"Идея завершена: {status.value}")


@router.get("/skips", response_model=list[SkipOut])
def skips(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[SkipOut]:
    rows = db.execute(
        select(IdeaSkip, TradeIdea)
        .join(TradeIdea, TradeIdea.id == IdeaSkip.idea_id)
        .order_by(IdeaSkip.skipped_at.desc())
        .limit(limit)
    ).all()
    return [
        SkipOut(
            idea_id=idea.id,
            instrument_id=idea.instrument_id,
            symbol=idea.instrument_id.split(":")[-1],
            direction=_value(idea.direction),
            strategy=_value(idea.strategy),
            score=idea.score,
            reason=_value(skip.reason),
            comment=skip.comment,
            skipped_at=skip.skipped_at,
        )
        for skip, idea in rows
    ]


@router.get("/misses", response_model=list[MissOut])
def misses(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[MissOut]:
    """Показанные идеи, завершившиеся без paper-сделки и явного skip.

    Это не второй журнал и не реконструкция по клиенту. Источником остаются
    ``trade_ideas`` + append-only ``idea_events``. Идеи с paper-сделкой уже
    видны во вкладке «Сделки», а явные отказы — в ``/skips``; исключаем оба
    класса, чтобы одна история не дублировалась двумя карточками.
    """
    terminal = [status for status in IdeaStatus if status.is_terminal]
    ideas = list(
        db.execute(
            select(TradeIdea)
            .where(
                TradeIdea.was_presented.is_(True),
                TradeIdea.status.in_(terminal),
                ~exists().where(PaperTrade.idea_id == TradeIdea.id),
                ~exists().where(IdeaSkip.idea_id == TradeIdea.id),
            )
            .order_by(TradeIdea.signal_time.desc())
            .limit(limit)
        ).scalars()
    )

    result: list[MissOut] = []
    for idea in ideas:
        status = IdeaStatus(idea.status)
        final_event = idea.events[-1] if idea.events else None
        reason_code = final_event.reason_code if final_event is not None else ""
        detail = final_event.reason_detail if final_event is not None else ""
        at = (
            final_event.occurred_at
            if final_event is not None and final_event.occurred_at is not None
            else idea.signal_time
        )
        result.append(
            MissOut(
                idea_id=idea.id,
                instrument_id=idea.instrument_id,
                symbol=idea.instrument_id.split(":")[-1],
                direction=_value(idea.direction),
                strategy=_value(idea.strategy),
                score=idea.score,
                status=status.value,
                reason=_miss_reason(status, reason_code),
                comment=detail,
                skipped_at=at,
            )
        )
    result.sort(key=lambda row: row.skipped_at, reverse=True)
    return result


__all__ = ["router"]
