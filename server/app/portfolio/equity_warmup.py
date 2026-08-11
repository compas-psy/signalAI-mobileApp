"""Targeted warm-up for Portfolio → Signals.

The generic market ingest has a shared first-load budget across every asset
class. After adding funds, bonds, futures and crypto, equities could spend many
scheduler cycles waiting for their first D1 history, while the Portfolio
Signals screen had nothing to rank. This job gives the investment action layer
its own small, bounded budget without delaying the short-term scanner.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..market import moex
from ..market.ingest import _board_path, store_candles
from ..market.investments import investment_universe
from ..models import Bar
from ..models.enums import AssetClass, Timeframe

MIN_TECHNICAL_D1 = 50
HISTORY_DAYS = 2200


def _count(session: Session, instrument_id: str) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(Bar).where(
                Bar.instrument_id == instrument_id,
                Bar.timeframe == Timeframe.D1,
                Bar.is_closed.is_(True),
            )
        ).scalar_one()
    )


def warm_equity_history(
    session: Session,
    *,
    budget: int = 6,
    now: datetime | None = None,
) -> str:
    """Backfill a bounded number of equities until D1 timing is measurable."""
    moment = now or datetime.now(UTC)
    equities = [
        item
        for item in investment_universe(session)
        if item.asset_class is AssetClass.EQUITY
        and item.instrument_id.startswith("MOEX:EQ:")
    ]
    needs = sorted(
        ((item, _count(session, item.instrument_id)) for item in equities),
        key=lambda pair: (pair[1], pair[0].symbol),
    )
    needs = [pair for pair in needs if pair[1] < MIN_TECHNICAL_D1]
    if not needs:
        return f"D1 по акциям готов: {len(equities)} инструментов"

    warmed = 0
    failed: list[str] = []
    since = (moment - timedelta(days=HISTORY_DAYS)).date()
    for instrument, before in needs[: max(1, budget)]:
        try:
            candles, _ = moex.candles(
                instrument.symbol,
                Timeframe.D1,
                since,
                path=_board_path(instrument),
            )
            store_candles(
                session,
                instrument.instrument_id,
                Timeframe.D1,
                candles,
            )
            after = _count(session, instrument.instrument_id)
            if after >= MIN_TECHNICAL_D1:
                warmed += 1
            else:
                failed.append(f"{instrument.symbol}:{after}")
        except Exception as exc:
            failed.append(f"{instrument.symbol}:{type(exc).__name__}")

    remaining = max(0, len(needs) - warmed)
    suffix = f"; проблемы {', '.join(failed[:4])}" if failed else ""
    return f"D1 warm-up: готовы +{warmed}, ещё требуют истории {remaining}{suffix}"


__all__ = ["MIN_TECHNICAL_D1", "warm_equity_history"]
