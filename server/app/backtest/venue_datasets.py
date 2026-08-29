"""Immutable dataset readiness for venue-scoped entry backtests.

Readiness is intentionally derived from published ``DatasetSnapshot`` metadata,
never from the live ``bars`` tail.  FORTS needs an explicit rolled contract
history and Bybit needs the point-in-time derivatives streams used by the
strategy.  Missing or short evidence therefore blocks a backtest instead of
creating a plausible-looking zero/PASS run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from ..models import DatasetSnapshot

BacktestVenue = Literal["FORTS", "BYBIT"]

BYBIT_DATASET_NAME = "entry-backtest-bybit"
FORTS_DATASET_NAME = "entry-backtest-forts"

# These are evidence requirements, not strategy thresholds.  A Bybit replay
# that has price but substitutes today's OI/funding into the past is leakage.
REQUIRED_BYBIT_STREAMS = (
    "price_h1",
    "price_d1",
    "mark_h1",
    "index_h1",
    "premium_h1",
    "open_interest",
    "funding",
)
# MOEX ISS has no intraday historical OI.  The dataset keeps the official D1
# OI series and the replay must retain OI_UNAVAILABLE on intraday bars rather
# than inventing a series that the exchange does not publish.
REQUIRED_FORTS_STREAMS = (
    "continuous_h1",
    "continuous_d1",
    "daily_open_interest",
)

_DATASET_BY_VENUE = {
    "BYBIT": BYBIT_DATASET_NAME,
    "FORTS": FORTS_DATASET_NAME,
}
_REQUIRED_BY_VENUE = {
    "BYBIT": REQUIRED_BYBIT_STREAMS,
    "FORTS": REQUIRED_FORTS_STREAMS,
}


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _months_between(start: datetime | None, end: datetime) -> int:
    if start is None:
        return 0
    months = (end.year - start.year) * 12 + end.month - start.month
    if end.day < start.day:
        months -= 1
    return max(0, months)


def _months_before(moment: datetime, months: int) -> datetime:
    absolute = moment.year * 12 + (moment.month - 1) - months
    year, month_index = divmod(absolute, 12)
    # Day one is deliberate: a dataset starting any later than the first day
    # of the boundary month has less than the configured full history span.
    return datetime(year, month_index + 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class BacktestDataReadiness:
    venue: BacktestVenue
    dataset_name: str
    snapshot_id: str | None
    status: str
    ready: bool
    required_months: int
    available_months: int
    period_from: datetime | None
    period_to: datetime | None
    stream_months: dict[str, int]
    blockers: tuple[str, ...]

    def as_json(self) -> dict:
        raw = asdict(self)
        raw["period_from"] = self.period_from.isoformat() if self.period_from else None
        raw["period_to"] = self.period_to.isoformat() if self.period_to else None
        raw["blockers"] = list(self.blockers)
        return raw


def assess_snapshot_readiness(
    snapshot: DatasetSnapshot | None,
    *,
    venue: BacktestVenue,
    now: datetime,
    required_months: int,
    max_staleness: timedelta = timedelta(days=7),
) -> BacktestDataReadiness:
    """Validate one published venue manifest without reading mutable live bars."""

    if venue not in _DATASET_BY_VENUE:
        raise ValueError("venue must be FORTS or BYBIT")
    _aware(now, "now")
    if required_months < 1:
        raise ValueError("required_months must be positive")
    expected_name = _DATASET_BY_VENUE[venue]
    if snapshot is None:
        return BacktestDataReadiness(
            venue=venue,
            dataset_name=expected_name,
            snapshot_id=None,
            status="MISSING_DATASET",
            ready=False,
            required_months=required_months,
            available_months=0,
            period_from=None,
            period_to=None,
            stream_months={},
            blockers=("DATASET_MISSING",),
        )

    watermark = dict(snapshot.source_watermark or {})
    blockers: list[str] = []
    if snapshot.dataset_name != expected_name:
        blockers.append("DATASET_NAME_MISMATCH")
    if str(watermark.get("venue") or "").upper() != venue:
        blockers.append("VENUE_MISMATCH")

    raw_streams = watermark.get("streams")
    streams = raw_streams if isinstance(raw_streams, dict) else {}
    boundary = _months_before(now, required_months)
    stream_months: dict[str, int] = {}
    required = _REQUIRED_BY_VENUE[venue]
    for name in required:
        raw = streams.get(name)
        if not isinstance(raw, dict):
            blockers.append(f"STREAM_MISSING:{name}")
            stream_months[name] = 0
            continue
        start = _parse_dt(raw.get("from"))
        end = _parse_dt(raw.get("to"))
        rows = raw.get("rows")
        digest = raw.get("content_sha256")
        artifact_key = raw.get("artifact_key")
        months = _months_between(start, now)
        stream_months[name] = months
        if start is None or start > boundary or months < required_months:
            blockers.append(f"STREAM_HISTORY_TOO_SHORT:{name}")
        if end is None or now - end > max_staleness:
            blockers.append(f"STREAM_STALE:{name}")
        if not isinstance(rows, int) or rows <= 0:
            blockers.append(f"STREAM_EMPTY:{name}")
        if not isinstance(digest, str) or len(digest) != 64:
            blockers.append(f"STREAM_HASH_INVALID:{name}")
        if not isinstance(artifact_key, str) or not artifact_key:
            blockers.append(f"STREAM_ARTIFACT_MISSING:{name}")

    if venue == "FORTS":
        if watermark.get("roll_boundaries_valid") is not True:
            blockers.append("ROLL_BOUNDARIES_INVALID")
        segment_count = watermark.get("segment_count")
        if not isinstance(segment_count, int) or segment_count < 2:
            blockers.append("CONTRACT_SEGMENTS_INSUFFICIENT")

    period_from = _parse_dt(watermark.get("period_from"))
    period_to = _parse_dt(watermark.get("period_to"))
    available = min(stream_months.values(), default=0)

    if not blockers:
        status = "READY"
    elif any(item.startswith("STREAM_MISSING:") for item in blockers):
        status = "INCOMPLETE_DATASET"
    elif any(
        item.startswith("STREAM_HISTORY_TOO_SHORT:") for item in blockers
    ):
        status = "INSUFFICIENT_HISTORY"
    elif any(item.startswith("STREAM_STALE:") for item in blockers):
        status = "STALE_DATASET"
    else:
        status = "INVALID_DATASET"

    return BacktestDataReadiness(
        venue=venue,
        dataset_name=expected_name,
        snapshot_id=str(snapshot.snapshot_id),
        status=status,
        ready=not blockers,
        required_months=required_months,
        available_months=available,
        period_from=period_from,
        period_to=period_to,
        stream_months=stream_months,
        blockers=tuple(dict.fromkeys(blockers)),
    )


def load_venue_readiness(
    session: Session,
    venue: BacktestVenue,
    *,
    now: datetime | None = None,
    required_months: int | None = None,
    cfg: EngineConfig | None = None,
) -> BacktestDataReadiness:
    """Resolve the latest point-in-time immutable manifest for one venue."""

    if venue not in _DATASET_BY_VENUE:
        raise ValueError("venue must be FORTS or BYBIT")
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    _aware(moment, "now")
    config = cfg or get_config()
    minimum = (
        int(required_months)
        if required_months is not None
        else int(config.get("backtest.walk_forward.min_history_months"))
    )
    dataset_name = _DATASET_BY_VENUE[venue]
    snapshot = session.execute(
        select(DatasetSnapshot)
        .where(
            DatasetSnapshot.dataset_name == dataset_name,
            DatasetSnapshot.tradable_at <= moment,
        )
        .order_by(
            DatasetSnapshot.tradable_at.desc(),
            DatasetSnapshot.created_at.desc(),
            DatasetSnapshot.snapshot_id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()
    return assess_snapshot_readiness(
        snapshot,
        venue=venue,
        now=moment,
        required_months=minimum,
    )


__all__ = [
    "BYBIT_DATASET_NAME",
    "FORTS_DATASET_NAME",
    "REQUIRED_BYBIT_STREAMS",
    "REQUIRED_FORTS_STREAMS",
    "BacktestDataReadiness",
    "BacktestVenue",
    "assess_snapshot_readiness",
    "load_venue_readiness",
]
