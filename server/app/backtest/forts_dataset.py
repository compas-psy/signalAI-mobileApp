"""Immutable point-in-time FORTS continuous datasets for entry backtests.

This module deliberately stops at evidence publication.  It never changes the
live universe, strategy thresholds, risk settings, TradeIdea rows or execution
state.  Historical collection supplies explicit contract segments; this layer
validates their coverage, builds real continuous bars and publishes them through
the shared content-addressed DatasetSnapshot contract.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Mapping, Sequence

from sqlalchemy.orm import Session

from ..datasets.snapshots import (
    DatasetRow,
    DatasetSnapshotBuilder,
    FilesystemSnapshotStore,
    SnapshotManifest,
    publish_snapshot,
)
from ..models import DatasetSnapshot
from .forts_continuous import FuturesSegment, build_continuous_futures

DATA_READY = "DATA_READY"
DATA_BLOCKED = "DATA_BLOCKED"
REQUIRED_FORTS_STREAMS: tuple[str, ...] = (
    "continuous_h1",
    "continuous_d1",
    "daily_open_interest",
)
_DEFAULT_END_TOLERANCE = timedelta(days=2)


@dataclass(frozen=True, slots=True)
class BuiltFortsDataset:
    status: str
    blockers: tuple[str, ...]
    manifest: SnapshotManifest


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _subtract_months(value: datetime, months: int) -> datetime:
    if months < 0:
        raise ValueError("months must not be negative")
    absolute = value.year * 12 + value.month - 1 - months
    year, month_zero = divmod(absolute, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _segment_metadata(segments: Sequence[FuturesSegment]) -> list[dict[str, str]]:
    return [
        {
            "contract_id": segment.contract_id,
            "valid_from": segment.valid_from.isoformat(),
            "valid_until": segment.valid_until.isoformat(),
        }
        for segment in sorted(segments, key=lambda item: item.valid_from)
    ]


def _chain_blockers(
    stream: str,
    segments: Sequence[FuturesSegment],
    *,
    required_start: datetime,
    end_at: datetime,
    end_tolerance: timedelta = _DEFAULT_END_TOLERANCE,
) -> list[str]:
    ordered = tuple(sorted(segments, key=lambda item: item.valid_from))
    if not ordered:
        return [f"NO_DATA:{stream}"]

    blockers: list[str] = []
    if ordered[0].valid_from > required_start or ordered[-1].valid_until < end_at:
        blockers.append(f"HISTORY_LT_36M:{stream}")
    if any(current.valid_from != previous.valid_until for previous, current in zip(ordered, ordered[1:])):
        blockers.append(f"ROLL_GAP:{stream}")

    # Contract metadata describes the intended chain, not the evidence actually
    # downloaded.  A nominal 36-month segment must never make a one-year candle
    # artifact look complete.  The continuous builder also removes bars outside
    # the half-open roll windows, so coverage is measured on the exact rows that
    # can participate in replay.
    observed = tuple(
        item.bar.open_time
        for item in build_continuous_futures(ordered)
    )
    if not observed:
        blockers.append(f"NO_ROWS:{stream}")
        return blockers
    if observed[0] > required_start:
        blockers.append(f"HISTORY_LT_36M:{stream}")
    if observed[-1] < end_at - end_tolerance:
        blockers.append(f"STALE_END:{stream}")
    return blockers


def _oi_blockers(
    series: Mapping[date, Decimal],
    *,
    required_start: datetime,
    end_at: datetime,
) -> list[str]:
    if not series:
        return ["NO_DATA:daily_open_interest"]
    days = sorted(series)
    # Daily OI for the current trading day is not guaranteed to be final until
    # the session has ended, so readiness requires evidence through yesterday.
    required_end_day = (end_at - timedelta(days=1)).date()
    if days[0] > required_start.date() or days[-1] < required_end_day:
        return ["HISTORY_LT_36M:daily_open_interest"]
    return []


def _bar_rows(
    stream: str,
    segments: Sequence[FuturesSegment],
    *,
    end_at: datetime,
    duration: timedelta,
) -> list[DatasetRow]:
    rows: list[DatasetRow] = []
    for item in build_continuous_futures(tuple(segments)):
        tradable_at = item.bar.open_time + duration
        # A closed flag alone is not enough to make a future period point-in-time
        # safe.  Synthetic fixtures and malformed providers are filtered here.
        if tradable_at > end_at:
            continue
        bar = item.bar
        rows.append(
            DatasetRow(
                key=f"{stream}|{bar.open_time.isoformat()}|{item.contract_id}",
                tradable_at=tradable_at,
                values={
                    "stream": stream,
                    "contract_id": item.contract_id,
                    "segment_valid_until": item.segment_valid_until,
                    "open_time": bar.open_time,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume_units": bar.volume_units,
                    "volume_notional": bar.volume_notional,
                    "open_interest": bar.open_interest,
                    "source": bar.source,
                    "quality_flags": bar.quality_flags,
                },
            )
        )
    return rows


def _oi_rows(series: Mapping[date, Decimal], *, end_at: datetime) -> list[DatasetRow]:
    rows: list[DatasetRow] = []
    for day, value in sorted(series.items()):
        # A daily history fact becomes safely replayable on the next UTC day.
        tradable_at = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1)
        if tradable_at > end_at:
            continue
        rows.append(
            DatasetRow(
                key=f"daily_open_interest|{day.isoformat()}",
                tradable_at=tradable_at,
                values={
                    "stream": "daily_open_interest",
                    "trading_day": day,
                    "open_interest": value,
                },
            )
        )
    return rows


def build_forts_manifest(
    *,
    root: str,
    start_at: datetime,
    end_at: datetime,
    h1_segments: Sequence[FuturesSegment],
    d1_segments: Sequence[FuturesSegment],
    daily_open_interest: Mapping[date, Decimal],
    min_history_months: int = 36,
) -> BuiltFortsDataset:
    """Build deterministic continuous H1/D1 + daily OI evidence for one root."""

    _aware(start_at, "start_at")
    _aware(end_at, "end_at")
    if end_at <= start_at:
        raise ValueError("end_at must be after start_at")
    if min_history_months <= 0:
        raise ValueError("min_history_months must be positive")
    normalized = root.strip().upper()
    if not normalized:
        raise ValueError("root must not be blank")

    required_start = _subtract_months(end_at, min_history_months)
    blockers = [
        *_chain_blockers(
            "continuous_h1",
            h1_segments,
            required_start=required_start,
            end_at=end_at,
        ),
        *_chain_blockers(
            "continuous_d1",
            d1_segments,
            required_start=required_start,
            end_at=end_at,
        ),
        *_oi_blockers(
            daily_open_interest,
            required_start=required_start,
            end_at=end_at,
        ),
    ]

    rows = [
        *_bar_rows(
            "continuous_h1",
            h1_segments,
            end_at=end_at,
            duration=timedelta(hours=1),
        ),
        *_bar_rows(
            "continuous_d1",
            d1_segments,
            end_at=end_at,
            duration=timedelta(days=1),
        ),
        *_oi_rows(daily_open_interest, end_at=end_at),
    ]
    if not any(row.values.get("stream") == "continuous_h1" for row in rows):
        blockers.append("NO_ROWS:continuous_h1")
    if not any(row.values.get("stream") == "continuous_d1" for row in rows):
        blockers.append("NO_ROWS:continuous_d1")
    if not any(row.values.get("stream") == "daily_open_interest" for row in rows):
        blockers.append("NO_ROWS:daily_open_interest")

    blockers_tuple = tuple(dict.fromkeys(blockers))
    status = DATA_READY if not blockers_tuple else DATA_BLOCKED
    h1_meta = _segment_metadata(h1_segments)
    d1_meta = _segment_metadata(d1_segments)
    roll_boundaries_valid = not any(item.startswith("ROLL_GAP:") for item in blockers_tuple)
    watermark = {
        "provider": "moex-iss-public",
        "root": normalized,
        "period_start": start_at,
        "period_end": end_at,
        "min_history_months": min_history_months,
        "required_streams": REQUIRED_FORTS_STREAMS,
        "readiness": status,
        "blockers": blockers_tuple,
        "roll_boundaries_valid": roll_boundaries_valid,
        "segment_count": max(len(h1_segments), len(d1_segments)),
        "segments": {
            "continuous_h1": h1_meta,
            "continuous_d1": d1_meta,
        },
    }
    manifest = DatasetSnapshotBuilder.build(
        dataset_name=f"forts:{normalized}:continuous",
        dataset_version="forts-continuous-v1",
        schema_version="1",
        tradable_at=end_at,
        source_watermark=watermark,
        rows=rows,
    )
    return BuiltFortsDataset(status=status, blockers=blockers_tuple, manifest=manifest)


def publish_forts_snapshot(
    session: Session,
    *,
    store: FilesystemSnapshotStore,
    built: BuiltFortsDataset,
    require_ready: bool = True,
) -> DatasetSnapshot:
    """Persist only immutable evidence; fail closed when 36m data is blocked."""

    if require_ready and built.status != DATA_READY:
        detail = ", ".join(built.blockers) or "unknown"
        raise ValueError(f"FORTS dataset is not ready: {detail}")
    return publish_snapshot(session, store=store, manifest=built.manifest)


__all__ = [
    "BuiltFortsDataset",
    "DATA_BLOCKED",
    "DATA_READY",
    "REQUIRED_FORTS_STREAMS",
    "build_forts_manifest",
    "publish_forts_snapshot",
]
