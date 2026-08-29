"""Canonical immutable Bybit research dataset assembly and readiness gates.

A backtest is allowed to start only from a content-addressed multi-stream
snapshot whose required inputs independently cover the requested history. This
prevents a long candle history from hiding a short funding/OI/reference-price
history and producing a deceptively complete result.

Network access is confined to ``collect_multistream``. Replay consumes the
published snapshot artifact only and never reaches this collector.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence

from sqlalchemy.orm import Session

from ..datasets.snapshots import (
    DatasetRow,
    DatasetSnapshotBuilder,
    FilesystemSnapshotStore,
    SnapshotManifest,
    publish_snapshot,
)
from ..market.http import FetchReport, http_json
from ..models import DatasetSnapshot
from ..models.enums import Timeframe
from .bybit_history import (
    HistoricalObservation,
    historical_funding,
    historical_index_price_klines,
    historical_klines,
    historical_long_short_ratio,
    historical_mark_price_klines,
    historical_open_interest,
    historical_premium_index_klines,
)

DATA_READY = "DATA_READY"
DATA_BLOCKED = "DATA_BLOCKED"

# Comprehensive R4 research dataset. A caller may explicitly narrow the set for
# a strategy with fewer dependencies, but the default can reproduce price,
# carry/basis, OI-flow and account-positioning experiments without live REST.
REQUIRED_BYBIT_STREAMS: tuple[str, ...] = (
    "klines",
    "funding",
    "open_interest",
    "mark_price",
    "index_price",
    "premium_index",
    "long_short_ratio",
)

_DEFAULT_END_TOLERANCE = timedelta(days=2)
# Funding is sparse (commonly 8h) while candles/OI can be hourly. A full-day
# pre-roll makes the coverage boundary independent of the wall-clock hour
# without putting pre-roll rows into the published research window.
_COLLECTION_PREROLL = timedelta(days=1)
_TIMEFRAME_DURATION = {
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
}
_TIMEFRAME_DERIVATIVE_INTERVAL = {
    Timeframe.M15: "15min",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
}


@dataclass(frozen=True, slots=True)
class StreamCoverage:
    stream: str
    ready: bool
    reason: str
    rows: int
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    first_tradable_at: datetime | None
    last_tradable_at: datetime | None

    def as_json(self) -> dict[str, object]:
        return {
            "stream": self.stream,
            "ready": self.ready,
            "reason": self.reason,
            "rows": self.rows,
            "first_observed_at": (
                None if self.first_observed_at is None else self.first_observed_at.isoformat()
            ),
            "last_observed_at": (
                None if self.last_observed_at is None else self.last_observed_at.isoformat()
            ),
            "first_tradable_at": (
                None if self.first_tradable_at is None else self.first_tradable_at.isoformat()
            ),
            "last_tradable_at": (
                None if self.last_tradable_at is None else self.last_tradable_at.isoformat()
            ),
        }


@dataclass(frozen=True, slots=True)
class BuiltBybitDataset:
    status: str
    coverage: tuple[StreamCoverage, ...]
    manifest: SnapshotManifest

    @property
    def blockers(self) -> tuple[StreamCoverage, ...]:
        return tuple(item for item in self.coverage if not item.ready)


@dataclass(frozen=True, slots=True)
class CollectedBybitDataset:
    built: BuiltBybitDataset
    reports: dict[str, tuple[FetchReport, ...]]

    @property
    def status(self) -> str:
        return self.built.status


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _subtract_months(value: datetime, months: int) -> datetime:
    if months < 0:
        raise ValueError("months must not be negative")
    absolute = value.year * 12 + (value.month - 1) - months
    year, month_zero = divmod(absolute, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _ordered_unique(
    rows: Iterable[HistoricalObservation],
) -> tuple[HistoricalObservation, ...]:
    by_time: dict[datetime, HistoricalObservation] = {}
    for row in rows:
        existing = by_time.get(row.observed_at)
        if existing is not None and existing != row:
            raise ValueError(
                f"conflicting historical observations at {row.observed_at.isoformat()}"
            )
        by_time[row.observed_at] = row
    return tuple(by_time[key] for key in sorted(by_time))


def _coverage(
    stream: str,
    rows: Sequence[HistoricalObservation],
    *,
    required_start: datetime,
    end_at: datetime,
    end_tolerance: timedelta,
) -> StreamCoverage:
    if not rows:
        return StreamCoverage(stream, False, "NO_DATA", 0, None, None, None, None)

    first = rows[0]
    last = rows[-1]
    if any(row.tradable_at > end_at for row in rows):
        reason = "FUTURE_FACT"
        ready = False
    elif first.observed_at > required_start:
        reason = "HISTORY_LT_36M"
        ready = False
    elif last.observed_at < end_at - end_tolerance:
        reason = "STALE_END"
        ready = False
    else:
        reason = "READY"
        ready = True

    return StreamCoverage(
        stream=stream,
        ready=ready,
        reason=reason,
        rows=len(rows),
        first_observed_at=first.observed_at,
        last_observed_at=last.observed_at,
        first_tradable_at=min(row.tradable_at for row in rows),
        last_tradable_at=max(row.tradable_at for row in rows),
    )


def build_multistream_manifest(
    *,
    symbol: str,
    start_at: datetime,
    end_at: datetime,
    streams: Mapping[str, Iterable[HistoricalObservation]],
    min_history_months: int = 36,
    required_streams: Sequence[str] = REQUIRED_BYBIT_STREAMS,
    end_tolerance: timedelta = _DEFAULT_END_TOLERANCE,
) -> BuiltBybitDataset:
    """Build a deterministic content-addressed snapshot and readiness report."""

    _aware(start_at, "start_at")
    _aware(end_at, "end_at")
    if end_at <= start_at:
        raise ValueError("end_at must be after start_at")
    if min_history_months <= 0:
        raise ValueError("min_history_months must be positive")
    if end_tolerance < timedelta(0):
        raise ValueError("end_tolerance must not be negative")
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("symbol must not be blank")

    required = tuple(dict.fromkeys(str(item).strip() for item in required_streams))
    if not required or any(not item for item in required):
        raise ValueError("required_streams must contain non-blank names")

    required_start = _subtract_months(end_at, min_history_months)
    coverage_start = min(start_at, required_start)

    coverage: list[StreamCoverage] = []
    dataset_rows: list[DatasetRow] = []

    for stream in required:
        ordered = _ordered_unique(streams.get(stream, ()))
        item = _coverage(
            stream,
            ordered,
            required_start=coverage_start,
            end_at=end_at,
            end_tolerance=end_tolerance,
        )
        coverage.append(item)
        for observation in ordered:
            if not (start_at <= observation.observed_at < end_at):
                continue
            dataset_rows.append(
                DatasetRow(
                    key=f"{stream}|{observation.observed_at.isoformat()}",
                    tradable_at=observation.tradable_at,
                    values={
                        "stream": stream,
                        "observed_at": observation.observed_at,
                        **observation.values,
                    },
                )
            )

    status = DATA_READY if all(item.ready for item in coverage) else DATA_BLOCKED
    watermark = {
        "provider": "bybit-v5-public",
        "symbol": normalized,
        "period_start": start_at,
        "period_end": end_at,
        "min_history_months": min_history_months,
        "required_streams": required,
        "readiness": status,
        "coverage": [item.as_json() for item in coverage],
    }
    manifest = DatasetSnapshotBuilder.build(
        dataset_name=f"bybit:{normalized}:multistream",
        dataset_version="bybit-multistream-v1",
        schema_version="1",
        tradable_at=end_at,
        source_watermark=watermark,
        rows=dataset_rows,
    )
    return BuiltBybitDataset(status=status, coverage=tuple(coverage), manifest=manifest)


def _candle_observations(candles, *, duration: timedelta) -> tuple[HistoricalObservation, ...]:
    rows: list[HistoricalObservation] = []
    for candle in candles:
        if not candle.is_closed:
            continue
        rows.append(
            HistoricalObservation(
                observed_at=candle.open_time,
                tradable_at=candle.open_time + duration,
                values={
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume_units": candle.volume_units,
                    "volume_notional": candle.volume_notional,
                    "open_interest": candle.open_interest,
                },
            )
        )
    return tuple(rows)


def collect_multistream(
    symbol: str,
    *,
    start_at: datetime,
    end_at: datetime,
    timeframe: Timeframe = Timeframe.H1,
    min_history_months: int = 36,
    required_streams: Sequence[str] = REQUIRED_BYBIT_STREAMS,
    fetch=http_json,
) -> CollectedBybitDataset:
    """Collect all required public streams and build one immutable manifest.

    A one-day pre-roll covers sparse streams such as funding even when the
    36-month boundary is not aligned to their timestamp grid. Pre-roll facts
    participate only in coverage validation; only rows inside the requested
    ``[start_at, end_at)`` window enter the content-addressed artifact.
    """

    _aware(start_at, "start_at")
    _aware(end_at, "end_at")
    if timeframe not in _TIMEFRAME_DURATION:
        raise ValueError(f"unsupported research timeframe: {timeframe.value}")
    duration = _TIMEFRAME_DURATION[timeframe]
    interval = _TIMEFRAME_DERIVATIVE_INTERVAL[timeframe]
    collection_start = start_at - max(duration, _COLLECTION_PREROLL)
    required = tuple(dict.fromkeys(required_streams))
    unknown = set(required) - set(REQUIRED_BYBIT_STREAMS)
    if unknown:
        raise ValueError(f"unknown Bybit dataset streams: {sorted(unknown)}")

    streams: dict[str, Iterable[HistoricalObservation]] = {}
    reports: dict[str, tuple[FetchReport, ...]] = {}

    if "klines" in required:
        candles, fetched = historical_klines(
            symbol,
            timeframe,
            start_at=collection_start,
            end_at=end_at,
            fetch=fetch,
        )
        streams["klines"] = _candle_observations(candles, duration=duration)
        reports["klines"] = fetched

    readers = {
        "mark_price": historical_mark_price_klines,
        "index_price": historical_index_price_klines,
        "premium_index": historical_premium_index_klines,
    }
    for stream, reader in readers.items():
        if stream not in required:
            continue
        rows, fetched = reader(
            symbol,
            timeframe,
            start_at=collection_start,
            end_at=end_at,
            fetch=fetch,
        )
        streams[stream] = tuple(rows)
        reports[stream] = fetched

    if "funding" in required:
        rows, fetched = historical_funding(
            symbol,
            start_at=collection_start,
            end_at=end_at,
            fetch=fetch,
        )
        streams["funding"] = tuple(rows)
        reports["funding"] = fetched

    if "open_interest" in required:
        rows, fetched = historical_open_interest(
            symbol,
            interval=interval,
            start_at=collection_start,
            end_at=end_at,
            fetch=fetch,
        )
        streams["open_interest"] = tuple(rows)
        reports["open_interest"] = fetched

    if "long_short_ratio" in required:
        rows, fetched = historical_long_short_ratio(
            symbol,
            period=interval,
            start_at=collection_start,
            end_at=end_at,
            fetch=fetch,
        )
        streams["long_short_ratio"] = tuple(rows)
        reports["long_short_ratio"] = fetched

    built = build_multistream_manifest(
        symbol=symbol,
        start_at=start_at,
        end_at=end_at,
        streams=streams,
        min_history_months=min_history_months,
        required_streams=required,
    )
    return CollectedBybitDataset(built=built, reports=reports)


def publish_multistream_snapshot(
    session: Session,
    *,
    store: FilesystemSnapshotStore,
    built: BuiltBybitDataset,
    require_ready: bool = True,
) -> DatasetSnapshot:
    """Persist the immutable manifest, failing closed when evidence is blocked."""

    if require_ready and built.status != DATA_READY:
        detail = ", ".join(
            f"{item.stream}:{item.reason}" for item in built.blockers
        ) or "unknown"
        raise ValueError(f"Bybit dataset is not ready: {detail}")
    return publish_snapshot(session, store=store, manifest=built.manifest)


__all__ = [
    "BuiltBybitDataset",
    "CollectedBybitDataset",
    "DATA_BLOCKED",
    "DATA_READY",
    "REQUIRED_BYBIT_STREAMS",
    "StreamCoverage",
    "build_multistream_manifest",
    "collect_multistream",
    "publish_multistream_snapshot",
]
