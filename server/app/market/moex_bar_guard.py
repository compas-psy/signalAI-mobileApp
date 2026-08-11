"""Time-based closure guard for MOEX candles.

MOEX ISS returns the current intraday candle in the same payload as historical
candles. The adapter historically marked every row ``is_closed=True``. That
made an 08:27 H1 candle look final even though it was still changing until
09:00, so the scanner could create an idea from a partial bar and the chart
later disagreed with the market.

This module fixes the data contract, not the strategy: H1/H4/D1 rules remain
the same, but a bar may participate only after it is actually closed. Forming
bars remain available to owner-facing display endpoints as display-only data.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
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
        # D1 represents a Moscow trading date. Without an exchange calendar,
        # today's D1 remains forming until the next Moscow date. This is
        # conservative and, unlike the old behaviour, can never leak a partial
        # daily bar into signal generation.
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
    """Remove legacy rows persisted as closed while they were still forming."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    intraday_cutoff = moment - timedelta(hours=5)
    day_cutoff = datetime.combine(
        moment.astimezone(Moscow).date(), datetime.min.time(), tzinfo=UTC
    )

    rows = list(
        session.execute(
            select(Bar)
            .join(Instrument, Instrument.instrument_id == Bar.instrument_id)
            .where(
                Instrument.venue == Venue.MOEX,
                Bar.is_closed.is_(True),
                or_(
                    and_(
                        Bar.timeframe.in_([Timeframe.M10, Timeframe.M15, Timeframe.H1]),
                        Bar.open_time >= intraday_cutoff,
                    ),
                    and_(
                        Bar.timeframe == Timeframe.D1,
                        Bar.open_time >= day_cutoff,
                    ),
                ),
            )
        ).scalars()
    )

    removed = 0
    for row in rows:
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
