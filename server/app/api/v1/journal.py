"""Серверный журнал владельца.

Thin-клиент не должен собирать историю из локальных файлов: источник истины —
PostgreSQL на VPS. Paper-сделки читаются отдельным endpoint `/paper/trades`,
а здесь лежат решения, которые сделки не создали: явные отказы владельца.
Секретов и изменяющих операций этот модуль не содержит.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import IdeaSkip, TradeIdea
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
            direction=str(idea.direction),
            strategy=str(idea.strategy),
            score=idea.score,
            reason=str(skip.reason),
            comment=skip.comment,
            skipped_at=skip.skipped_at,
        )
        for skip, idea in rows
    ]


__all__ = ["router"]
