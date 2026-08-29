"""Canonical immutable Bybit research dataset assembly and readiness gates.

A backtest is allowed to start only from a content-addressed multi-stream
snapshot whose required inputs independently cover the requested history. This
prevents a long candle history from hiding a short funding/OI/reference-price
history and producing a deceptively complete result.
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
from ..models import DatasetSnapshot
from .bybit_history import HistoricalObservation

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

# A stream can finish slightly before the exclusive dataset end because data is
# sampled on discrete intervals. Two days safely covers the coarsest 1D stream
# while still rejecting genuinely stale histories.
_DEFAULT_END_TOLERANCE = timedelta(days=2)


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
    """Build a deterministic content-addressed snapshot and readiness report.

    ``start_at`` describes the intended research window. Readiness is based on
    the stricter of that boundary and ``end_at - min_history_months``: callers
    cannot request a short window and accidentally satisfy a 36-month gate.
    """

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
    # If the explicitly requested dataset begins earlier than the minimum gate,
    # require that earlier boundary as well so readiness means full requested
    # coverage, not merely the policy minimum.
    coverage_start = min(start_at, required_start)

    canonical: dict[str, tuple[HistoricalObservation, ...]] = {}
    coverage: list[StreamCoverage] = []
    dataset_rows: list[DatasetRow] = []

    for stream in required:
        ordered = _ordered_unique(streams.get(stream, ()))
        canonical[stream] = ordered
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
                # Snapshot identity only includes the requested research window;
                # out-of-window rows cannot silently influence replay.
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

    # Supplied but non-required streams are intentionally excluded. Strategy
    # dependency declarations, not incidental downloader output, define replay.
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
    "DATA_BLOCKED",
    "DATA_READY",
    "REQUIRED_BYBIT_STREAMS",
    "StreamCoverage",
    "build_multistream_manifest",
    "publish_multistream_snapshot",
]
