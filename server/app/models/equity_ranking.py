"""Stable daily ranking of investment-universe equities.

The owner-facing Portfolio → Signals screen must never depend on a rare
research hypothesis to have something useful to show.  This table stores one
immutable ranking snapshot per Moscow market day.  The scheduler refreshes it
once a day; API reads are therefore cheap and stable throughout the day.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UuidPk, utcnow_column


class EquityRankingSnapshot(UuidPk, Base):
    __tablename__ = "equity_ranking_snapshots"

    market_day: Mapped[date] = mapped_column(Date, nullable=False)
    generated_at: Mapped[datetime] = utcnow_column()
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    universe_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scored_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    methodology: Mapped[str] = mapped_column(
        String(32), nullable=False, default="equity_rank_v1"
    )
    items_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (
        UniqueConstraint("market_day", name="uq_equity_ranking_snapshots_market_day"),
    )


__all__ = ["EquityRankingSnapshot"]
