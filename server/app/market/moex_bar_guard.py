"""Time-based closure guard for MOEX candles.

MOEX ISS returns the current intraday candle in the same payload as historical
candles.  The adapter historically marked every row ``is_closed=True``.  That
made an 08:27 H1 candle look final even though it was still changing until
09:00, so the scanner could create an idea from a partial bar and the chart
later disagreed with the market.

This module fixes the *data contract*, not the strategy: a setup still uses the
same H1/H4/D1 rules, but only after the corresponding bar is actually closed.
The forming bar remains available to owner-facing display endpoints as
``display-only`` data.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Bar, Instrument
from ..models.enums import Timeframe, Venue
from . import moex
from .candles import Candle

Moscow = ZoneInfo("Europe/Moscow")
_RAW_CANDLES = moex.candles


def is_time_closed(
    candle: Candle,
    timeframe: Timeframe,
    *,
    now: datetime | None = None,
) -> bool:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)

    if timeframe is Timeframe.M10:
        return candle.open_time + timedelta(minutes=10) <= moment
    if timeframe is Timeframe.M15:
        return candle.open_time + timedelta(minutes=15) <= moment
    if timeframe is Timeframe.H1:
        return candle.open_time + timedelta(hours=1) <= moment
    if timeframe is Timeframe.H4:
        return candle.open_time + timedelta(hours=4) <= moment
    if timeframe is Timeframe.D1:
        # D1 represents a Moscow trading date.  Without an exchange calendar
        # the only safe fallback is to call today's daily candle forming until
        # the next Moscow date.  Historical D1 is unambiguous.
        return candle.open_time.date() < moment.astimezone(Moscow).date()
    return candle.is_closed


def guarded_candles(*args, **kwargs):
    """Drop-in replacement for :func:`moex.candles` with honest closure."""
    candles, reports = _RAW_CANDLES(*args, **kwargs)
    timeframe = kwargs.get("timeframe")
    if timeframe is None and len(args) >= 2:
        timeframe = args[1]
    if timeframe is None:
        return candles, reports
    return [
        replace(candle, is_closed=is_time_closed(candle, timeframe))
        for candle in candles
    ], reports


def install() -> None:
    """Install once in the scheduler process before ingest imports run."""
    if moex.candles is not guarded_candles:
        moex.candles = guarded_candles


def purge_premature_moex_bars(
    session: Session,
    *,
    now: datetime | None = None,
) -> int:
    """Remove legacy rows that were persisted as closed while still forming.

    Only the currently possibly-forming tail is inspected; historical rows are
    untouched.  After this one-time cleanup the guarded adapter prevents the
    problem from recurring.
    """
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    cutoff = moment - timedelta(hours=5)

    rows = list(
        session.execute(
            select(Bar)
            .join(Instrument, Instrument.instrument_id == Bar.instrument_id)
            .where(
                Instrument.venue == Venue.MOEX,
                Bar.is_closed.is_(True),
                Bar.timeframe.in_([Timeframe.M10, Timeframe.M15, Timeframe.H1, Timeframe.D1]),
                Bar.open_time >= cutoff if False else True,
            )
        ).scalars()
    )

    # The D1 candidate can be older than the 5h tail, so filter in Python.
    removed = 0
    today_moscow = moment.astimezone(Moscow).date()
    for row in rows:
        if row.timeframe is Timeframe.D1:
            candidate = row.open_time.date() == today_moscow
        else:
            candidate = row.open_time >= cutoff
        if not candidate:
            continue
        candle = Candle(
            open_time=row.open_time,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume_units=row.volume_units,
            volume_notional=row.volume_notional,
            open_interest=row.open_interest,
            is_closed=True,
            source=str(row.source),
            quality_flags=tuple(row.quality_flags or []),
        )
        if is_time_closed(candle, row.timeframe, now=moment):
            continue
        session.delete(row)
        removed += 1
    if removed:
        session.flush()
    return removed


__all__ = ["guarded_candles", "install", "is_time_closed", "purge_premature_moex_bars"]
