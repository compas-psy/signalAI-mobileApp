"""Daily company ranking for Portfolio → Signals."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...db import get_db
from ...investment_radar import latest
from ...schemas.common import ApiModel

router = APIRouter(prefix="/investment-radar", tags=["portfolio"])


class EquityRadarCardOut(ApiModel):
    instrument_id: str
    symbol: str
    title: str
    score: float
    label: str
    latest_price: float | None = None
    return_20d_percent: float | None = None
    return_60d_percent: float | None = None
    research_score: float
    technical_score: float
    liquidity_score: float
    median_daily_notional_rub: float | None = None
    has_research_hypothesis: bool = False
    thesis: str = ""
    reasons: list[str] = []
    warnings: list[str] = []


class EquityRadarOut(ApiModel):
    generated_at: str
    as_of: str | None = None
    cadence: str
    method: str
    cards: list[EquityRadarCardOut] = []


@router.get("", response_model=EquityRadarOut)
def equity_radar(session: Session = Depends(get_db)) -> EquityRadarOut:
    """Return the latest daily snapshot, always including weak companies."""
    snapshot = latest(session)
    # get_db commits successful requests, so a first-use snapshot is durable.
    return EquityRadarOut.model_validate(snapshot)
