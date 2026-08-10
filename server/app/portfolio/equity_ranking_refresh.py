"""Refresh policy for the daily Portfolio → Signals ranking.

The ranking must follow new *closed equity D1 data*, not the wall clock.  A
calendar-only trigger can create Monday's snapshot just after midnight from
Friday's candles and then keep it all Monday because the row for that date
already exists.  This guard keeps the last good snapshot until a newer closed
MOEX equity D1 arrives, then rebuilds once.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..market.investments import investment_universe
from ..models import Bar
from ..models.enums import AssetClass, Timeframe
from .equity_ranking import build_daily_ranking, latest_snapshot, summary


def latest_equity_d1(session: Session) -> datetime | None:
    ids = [
        instrument.instrument_id
        for instrument in investment_universe(session)
        if instrument.asset_class is AssetClass.EQUITY
        and instrument.instrument_id.startswith("MOEX:EQ:")
    ]
    if not ids:
        return None
    return session.execute(
        select(func.max(Bar.open_time)).where(
            Bar.instrument_id.in_(ids),
            Bar.timeframe == Timeframe.D1,
            Bar.is_closed.is_(True),
        )
    ).scalar_one_or_none()


def refresh(session: Session) -> str:
    """Rebuild only when tracked equities have a newer closed daily bar."""
    newest = latest_equity_d1(session)
    if newest is None:
        return "закрытых D1 по акциям нет — рейтинг оставлен без изменений"

    previous = latest_snapshot(session)
    previous_as_of = previous.data_as_of if previous is not None else None
    if previous_as_of is not None and newest <= previous_as_of:
        return (
            f"новых D1 по акциям после {previous_as_of.isoformat()} нет — "
            "рейтинг оставлен без изменений"
        )

    # force matters after an unusual restart/manual warm-up earlier on the
    # same Moscow date: if a fresh D1 arrives later, replace that day's stale
    # snapshot instead of returning it due to the date uniqueness guard.
    snapshot = build_daily_ranking(session, force=previous is not None)
    return summary(snapshot)


__all__ = ["latest_equity_d1", "refresh"]
