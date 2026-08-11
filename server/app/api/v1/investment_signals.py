"""Portfolio → Signals API.

Unlike the diagnostic full-market ranking, this endpoint returns the compact
set of investment decisions the owner actually asked the product to surface.
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy.orm import Session

from ...db import get_db
from ...portfolio.investment_signals import build_investment_signals
from ...schemas.common import ApiModel

router = APIRouter(prefix="/research", tags=["research"])


class InvestmentSignalOut(ApiModel):
    rank: int
    instrument_id: str
    symbol: str
    title: str
    action: str
    action_label: str
    score: float
    price: float | None = None
    horizon: str
    fundamental_score: float
    technical_score: float
    technical_state: str
    catalyst_adjustment: float = 0
    why: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    catalyst: dict | None = None


class InvestmentSignalsOut(ApiModel):
    market_day: date | None = None
    generated_at: datetime | None = None
    data_as_of: datetime | None = None
    methodology: str = "investment_signal_v1"
    universe_count: int = 0
    scored_count: int = 0
    eligible_count: int = 0
    signals: list[InvestmentSignalOut] = Field(default_factory=list)
    reason: str = ""


@router.get("/equity-signals", response_model=InvestmentSignalsOut)
def equity_signals(
    limit: int = Query(12, ge=1, le=30),
    session: Session = Depends(get_db),
) -> InvestmentSignalsOut:
    try:
        snapshot, signals, reason = build_investment_signals(session, limit=limit)
        # ensure_current_ranking may repair a stale/empty warm-up snapshot.
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(
            503,
            f"инвестиционные сигналы пока не собраны: {type(exc).__name__}: {exc}",
        ) from None

    if snapshot is None:
        return InvestmentSignalsOut(reason=reason)

    items = list(snapshot.items_json or [])
    return InvestmentSignalsOut(
        market_day=snapshot.market_day,
        generated_at=snapshot.generated_at,
        data_as_of=snapshot.data_as_of,
        universe_count=snapshot.universe_count,
        scored_count=snapshot.scored_count,
        eligible_count=sum(bool(item.get("eligible")) for item in items),
        signals=[InvestmentSignalOut(**signal.as_dict()) for signal in signals],
        reason=reason,
    )
