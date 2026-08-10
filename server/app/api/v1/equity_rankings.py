"""Daily company ranking for Portfolio → Signals.

The endpoint is read-only from the user's point of view: no BUY/SELL side,
position size or order fields exist in the response.  The scheduler normally
builds the snapshot after midnight Moscow time; the first request can recover
a missing snapshot after a restart by building it once.
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy.orm import Session

from ...db import get_db
from ...portfolio.equity_ranking import build_daily_ranking, latest_snapshot
from ...schemas.common import ApiModel

router = APIRouter(prefix="/research", tags=["research"])


class EquityRankingItemOut(ApiModel):
    rank: int
    instrument_id: str
    symbol: str
    title: str
    score: float
    tier: str
    eligible: bool
    fundamental_score: float
    technical_score: float
    catalyst_adjustment: float = 0
    technical_state: str = ""
    price: float | None = None
    momentum_3m: float | None = None
    momentum_6m: float | None = None
    drawdown_6m: float | None = None
    volatility_3m: float | None = None
    fundamental_facts: list[str] = Field(default_factory=list)
    technical_facts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    hypothesis: dict | None = None


class EquityRankingOut(ApiModel):
    market_day: date
    generated_at: datetime
    data_as_of: datetime | None = None
    methodology: str
    universe_count: int
    scored_count: int
    items: list[EquityRankingItemOut] = Field(default_factory=list)
    reason: str = ""


def _out(snapshot) -> EquityRankingOut:
    items = [EquityRankingItemOut(**item) for item in (snapshot.items_json or [])]
    reason = ""
    if not items:
        reason = (
            "инвестиционная вселенная акций пока пуста или дневные данные ещё "
            "не загружены; рейтинг будет пересобран планировщиком"
        )
    return EquityRankingOut(
        market_day=snapshot.market_day,
        generated_at=snapshot.generated_at,
        data_as_of=snapshot.data_as_of,
        methodology=snapshot.methodology,
        universe_count=snapshot.universe_count,
        scored_count=snapshot.scored_count,
        items=items,
        reason=reason,
    )


@router.get("/equity-ranking", response_model=EquityRankingOut)
def equity_ranking(session: Session = Depends(get_db)) -> EquityRankingOut:
    snapshot = latest_snapshot(session)
    if snapshot is None:
        try:
            snapshot = build_daily_ranking(session)
            session.commit()
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                503,
                f"дневной рейтинг ещё не собран: {type(exc).__name__}: {exc}",
            ) from None
    return _out(snapshot)
