"""Immutable multi-stream Bybit research dataset assembly.

The builder accepts already-fetched historical streams and converts them to the
shared content-addressed DatasetSnapshot contract. It performs no network I/O
and never writes canonical live bars.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable

from ..datasets.snapshots import DatasetRow, DatasetSnapshotBuilder, SnapshotManifest
from ..market.candles import Candle
from ..market.derivatives import FundingObservation
from .bybit_history import HistoricalOpenInterest, HistoricalReferenceBar


_H1 = timedelta(hours=1)
_D1 = timedelta(days=1)


def _bounds(start_at: datetime, end_at: datetime) -> None:
    for name, value in (("start_at", start_at), ("end_at", end_at)):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")
    if end_at <= start_at:
        raise ValueError("end_at must be after start_at")


def _symbol(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError("symbol must not be blank")
    return normalized


def _inside(at: datetime, *, start_at: datetime, end_at: datetime, label: str) -> None:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    if not start_at <= at < end_at:
        raise ValueError(f"{label} lies outside requested history window")


def _not_future(tradable_at: datetime, *, end_at: datetime, label: str) -> None:
    if tradable_at.tzinfo is None or tradable_at.utcoffset() is None:
        raise ValueError(f"{label} tradable_at must be timezone-aware")
    if tradable_at > end_at:
        raise ValueError(f"{label} becomes tradable after snapshot boundary")


def _candle_values(stream: str, candle: Candle) -> dict[str, object]:
    return {
        "stream": stream,
        "open_time": candle.open_time,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume_units": candle.volume_units,
        "volume_notional": candle.volume_notional,
        "open_interest": candle.open_interest,
        "source": candle.source,
        "quality_flags": list(candle.quality_flags),
    }


def _trade_rows(
    symbol: str,
    candles: Iterable[Candle],
    *,
    stream: str,
    duration: timedelta,
    start_at: datetime,
    end_at: datetime,
) -> list[DatasetRow]:
    rows: list[DatasetRow] = []
    for candle in candles:
        if not isinstance(candle, Candle):
            raise ValueError(f"{stream} rows must be Candle")
        if not candle.is_closed:
            raise ValueError(f"{stream} contains an unclosed candle")
        _inside(candle.open_time, start_at=start_at, end_at=end_at, label=stream)
        tradable_at = candle.open_time + duration
        _not_future(tradable_at, end_at=end_at, label=stream)
        rows.append(
            DatasetRow(
                key=f"{symbol}:{stream}:{candle.open_time.isoformat()}",
                tradable_at=tradable_at,
                values=_candle_values(stream, candle),
            )
        )
    return rows


def _reference_rows(
    symbol: str,
    values: Iterable[HistoricalReferenceBar],
    *,
    stream: str,
    expected_reference: str,
    start_at: datetime,
    end_at: datetime,
) -> list[DatasetRow]:
    rows: list[DatasetRow] = []
    for item in values:
        if not isinstance(item, HistoricalReferenceBar):
            raise ValueError(f"{stream} rows must be HistoricalReferenceBar")
        if item.stream != expected_reference:
            raise ValueError(
                f"{stream} expected reference stream {expected_reference}, got {item.stream}"
            )
        _inside(item.open_time, start_at=start_at, end_at=end_at, label=stream)
        _not_future(item.tradable_at, end_at=end_at, label=stream)
        rows.append(
            DatasetRow(
                key=f"{symbol}:{stream}:{item.open_time.isoformat()}",
                tradable_at=item.tradable_at,
                values={
                    "stream": stream,
                    "reference_stream": item.stream,
                    "open_time": item.open_time,
                    "open": item.open,
                    "high": item.high,
                    "low": item.low,
                    "close": item.close,
                },
            )
        )
    return rows


def build_bybit_history_snapshot(
    *,
    symbol: str,
    start_at: datetime,
    end_at: datetime,
    trade_h1: Iterable[Candle],
    trade_d1: Iterable[Candle],
    mark_h1: Iterable[HistoricalReferenceBar],
    index_h1: Iterable[HistoricalReferenceBar],
    premium_h1: Iterable[HistoricalReferenceBar],
    open_interest: Iterable[HistoricalOpenInterest],
    funding: Iterable[FundingObservation],
    source_watermark: dict[str, object],
) -> SnapshotManifest:
    """Build one deterministic Bybit history snapshot for a symbol."""

    _bounds(start_at, end_at)
    normalized = _symbol(symbol)
    rows: list[DatasetRow] = []
    rows.extend(
        _trade_rows(
            normalized,
            trade_h1,
            stream="trade_h1",
            duration=_H1,
            start_at=start_at,
            end_at=end_at,
        )
    )
    rows.extend(
        _trade_rows(
            normalized,
            trade_d1,
            stream="trade_d1",
            duration=_D1,
            start_at=start_at,
            end_at=end_at,
        )
    )
    rows.extend(
        _reference_rows(
            normalized,
            mark_h1,
            stream="mark_h1",
            expected_reference="mark",
            start_at=start_at,
            end_at=end_at,
        )
    )
    rows.extend(
        _reference_rows(
            normalized,
            index_h1,
            stream="index_h1",
            expected_reference="index",
            start_at=start_at,
            end_at=end_at,
        )
    )
    rows.extend(
        _reference_rows(
            normalized,
            premium_h1,
            stream="premium_h1",
            expected_reference="premium",
            start_at=start_at,
            end_at=end_at,
        )
    )

    for item in open_interest:
        if not isinstance(item, HistoricalOpenInterest):
            raise ValueError("open_interest rows must be HistoricalOpenInterest")
        _inside(
            item.observed_at,
            start_at=start_at,
            end_at=end_at,
            label="open_interest",
        )
        _not_future(item.tradable_at, end_at=end_at, label="open_interest")
        rows.append(
            DatasetRow(
                key=f"{normalized}:open_interest:{item.observed_at.isoformat()}",
                tradable_at=item.tradable_at,
                values={
                    "stream": "open_interest",
                    "observed_at": item.observed_at,
                    "open_interest": item.open_interest,
                },
            )
        )

    for item in funding:
        if not isinstance(item, FundingObservation):
            raise ValueError("funding rows must be FundingObservation")
        _inside(
            item.settled_at,
            start_at=start_at,
            end_at=end_at,
            label="funding",
        )
        _not_future(item.tradable_at, end_at=end_at, label="funding")
        rows.append(
            DatasetRow(
                key=f"{normalized}:funding:{item.settled_at.isoformat()}",
                tradable_at=item.tradable_at,
                values={
                    "stream": "funding",
                    "settled_at": item.settled_at,
                    "rate": item.rate,
                    "source": item.source,
                },
            )
        )

    watermark = dict(source_watermark)
    watermark.update(
        {
            "schema": "bybit_history_multistream_v1",
            "symbol": normalized,
            "start_at": start_at,
            "end_at": end_at,
        }
    )
    return DatasetSnapshotBuilder.build(
        dataset_name=f"bybit_history:{normalized}",
        dataset_version="bybit_history_multistream_v1",
        schema_version="1",
        tradable_at=end_at,
        source_watermark=watermark,
        rows=rows,
    )


__all__ = ["build_bybit_history_snapshot"]
