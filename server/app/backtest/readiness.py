"""Fail-closed data readiness for venue-specific entry backtests.

This module does not run a strategy, mutate admission, or promote anything. It
answers the prerequisite question that was previously invisible: do we have
sufficient closed D1/H1 history to run the configured walk-forward honestly?
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from ..models import Bar, Instrument
from ..models.enums import AssetClass, Timeframe, Venue

BacktestVenue = Literal["FORTS", "BYBIT"]


def _months_between(start: datetime | None, end: datetime) -> int:
    if start is None:
        return 0
    months = (end.year - start.year) * 12 + end.month - start.month
    if end.day < start.day:
        months -= 1
    return max(0, months)


def _months_back(moment: datetime, months: int) -> tuple[int, int]:
    absolute = moment.year * 12 + (moment.month - 1) - months
    return absolute // 12, absolute % 12 + 1


@dataclass(frozen=True, slots=True)
class BacktestDataReadiness:
    venue: BacktestVenue
    status: str
    ready: bool
    required_months: int
    available_d1_months: int
    available_h1_months: int
    earliest_d1: datetime | None
    latest_d1: datetime | None
    earliest_h1: datetime | None
    latest_h1: datetime | None
    blockers: tuple[str, ...]

    def as_json(self) -> dict:
        raw = asdict(self)
        for key in ("earliest_d1", "latest_d1", "earliest_h1", "latest_h1"):
            value = raw[key]
            raw[key] = value.isoformat() if value is not None else None
        raw["blockers"] = list(self.blockers)
        return raw


def assess_history_bounds(
    *,
    venue: BacktestVenue,
    now: datetime,
    required_months: int,
    earliest_d1: datetime | None,
    latest_d1: datetime | None,
    earliest_h1: datetime | None,
    latest_h1: datetime | None,
) -> BacktestDataReadiness:
    if venue not in {"FORTS", "BYBIT"}:
        raise ValueError("venue must be FORTS or BYBIT")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if required_months < 1:
        raise ValueError("required_months must be positive")

    d1_months = _months_between(earliest_d1, now)
    h1_months = _months_between(earliest_h1, now)
    blockers: list[str] = []
    if earliest_d1 is None or d1_months < required_months:
        blockers.append("D1_HISTORY_TOO_SHORT")
    if earliest_h1 is None or h1_months < required_months:
        blockers.append("H1_HISTORY_TOO_SHORT")

    # A long span ending far in the past is not usable current research data.
    required_year, required_month = _months_back(now, required_months)
    required_boundary = datetime(required_year, required_month, 1, tzinfo=UTC)
    if earliest_d1 is not None and earliest_d1 > required_boundary:
        if "D1_HISTORY_TOO_SHORT" not in blockers:
            blockers.append("D1_HISTORY_TOO_SHORT")
    if earliest_h1 is not None and earliest_h1 > required_boundary:
        if "H1_HISTORY_TOO_SHORT" not in blockers:
            blockers.append("H1_HISTORY_TOO_SHORT")

    ready = not blockers
    return BacktestDataReadiness(
        venue=venue,
        status="READY" if ready else "INSUFFICIENT_HISTORY",
        ready=ready,
        required_months=required_months,
        available_d1_months=d1_months,
        available_h1_months=h1_months,
        earliest_d1=earliest_d1,
        latest_d1=latest_d1,
        earliest_h1=earliest_h1,
        latest_h1=latest_h1,
        blockers=tuple(blockers),
    )


def _scope(venue: BacktestVenue) -> tuple[Venue, AssetClass]:
    if venue == "FORTS":
        return Venue.MOEX, AssetClass.FUTURES
    if venue == "BYBIT":
        return Venue.CRYPTO, AssetClass.CRYPTO_PERPETUAL
    raise ValueError("venue must be FORTS or BYBIT")


def _bounds(
    session: Session,
    *,
    venue: Venue,
    asset_class: AssetClass,
    timeframe: Timeframe,
) -> tuple[datetime | None, datetime | None]:
    row = session.execute(
        select(func.min(Bar.open_time), func.max(Bar.open_time))
        .select_from(Bar)
        .join(Instrument, Instrument.instrument_id == Bar.instrument_id)
        .where(
            Instrument.venue == venue,
            Instrument.asset_class == asset_class,
            Bar.timeframe == timeframe,
            Bar.is_closed.is_(True),
        )
    ).one()
    return row[0], row[1]


def load_venue_readiness(
    session: Session,
    venue: BacktestVenue,
    *,
    now: datetime | None = None,
    cfg: EngineConfig | None = None,
) -> BacktestDataReadiness:
    moment = now or datetime.now(UTC)
    config = cfg or get_config()
    venue_enum, asset_class = _scope(venue)
    earliest_d1, latest_d1 = _bounds(
        session, venue=venue_enum, asset_class=asset_class, timeframe=Timeframe.D1
    )
    earliest_h1, latest_h1 = _bounds(
        session, venue=venue_enum, asset_class=asset_class, timeframe=Timeframe.H1
    )
    return assess_history_bounds(
        venue=venue,
        now=moment,
        required_months=int(config.get("backtest.walk_forward.min_history_months")),
        earliest_d1=earliest_d1,
        latest_d1=latest_d1,
        earliest_h1=earliest_h1,
        latest_h1=latest_h1,
    )


__all__ = [
    "BacktestDataReadiness",
    "BacktestVenue",
    "assess_history_bounds",
    "load_venue_readiness",
]
