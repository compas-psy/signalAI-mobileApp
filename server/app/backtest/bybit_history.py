"""Research-only deep historical Bybit market-data readers.

These readers are deliberately outside the live market-ingestion path. They
page public Bybit history for immutable backtest datasets and never write
canonical ``bars``, create ``TradeIdea`` rows or change runtime scanner
behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from ..market.candles import Candle
from ..market.crypto import BASE, INTERVALS, _parse_kline_rows, _result
from ..market.derivatives import FundingObservation
from ..market.http import FetchReport, http_json
from ..models.enums import Timeframe

_TIMEFRAME_DURATION: dict[Timeframe, timedelta] = {
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
}
_REFERENCE_PATHS = {
    "mark": "mark-price-kline",
    "index": "index-price-kline",
    "premium": "premium-index-price-kline",
}


@dataclass(frozen=True, slots=True)
class HistoricalReferenceBar:
    stream: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    tradable_at: datetime


@dataclass(frozen=True, slots=True)
class HistoricalOpenInterest:
    observed_at: datetime
    open_interest: Decimal
    tradable_at: datetime


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _bounds(start_at: datetime, end_at: datetime) -> None:
    _aware(start_at, "start_at")
    _aware(end_at, "end_at")
    if end_at <= start_at:
        raise ValueError("end_at must be after start_at")


def _symbol(value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError("symbol must not be blank")
    return normalized


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _millis(value: object) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(str(value)) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def historical_klines(
    symbol: str,
    timeframe: Timeframe,
    *,
    start_at: datetime,
    end_at: datetime,
    page_limit: int = 1000,
    category: str = "linear",
    fetch=http_json,
) -> tuple[list[Candle], tuple[FetchReport, ...]]:
    """Read closed trade candles in ``[start_at, end_at)`` using backward paging."""

    _bounds(start_at, end_at)
    normalized = _symbol(symbol)
    if timeframe not in INTERVALS:
        raise ValueError(f"unsupported Bybit historical timeframe: {timeframe.value}")

    safe_limit = max(1, min(int(page_limit), 1000))
    duration = _TIMEFRAME_DURATION[timeframe]
    cursor_end_ms = int(end_at.timestamp() * 1000)
    collected: dict[datetime, Candle] = {}
    reports: list[FetchReport] = []

    while True:
        url = (
            f"{BASE}/v5/market/kline?category={category}&symbol={normalized}"
            f"&interval={INTERVALS[timeframe]}&limit={safe_limit}&end={cursor_end_ms}"
        )
        payload, report = fetch(url)
        reports.append(report)
        rows = _result(payload).get("list") or []
        if not rows:
            break

        page = _parse_kline_rows(
            rows,
            duration=duration,
            moment=end_at,
            source="bybit-history",
        )
        if not page:
            break

        for candle in page:
            if candle.is_closed and start_at <= candle.open_time < end_at:
                collected[candle.open_time] = candle

        oldest = min(candle.open_time for candle in page)
        if oldest <= start_at:
            break
        next_cursor = int(oldest.timestamp() * 1000) - 1
        if next_cursor >= cursor_end_ms:
            raise RuntimeError("Bybit historical cursor did not move backward")
        cursor_end_ms = next_cursor

    return [collected[key] for key in sorted(collected)], tuple(reports)


def historical_reference_klines(
    symbol: str,
    timeframe: Timeframe,
    *,
    stream: str,
    start_at: datetime,
    end_at: datetime,
    page_limit: int = 1000,
    category: str = "linear",
    fetch=http_json,
) -> tuple[list[HistoricalReferenceBar], tuple[FetchReport, ...]]:
    """Read historical mark/index/premium OHLC in ``[start_at, end_at)``."""

    _bounds(start_at, end_at)
    normalized = _symbol(symbol)
    if timeframe not in INTERVALS:
        raise ValueError(f"unsupported Bybit historical timeframe: {timeframe.value}")
    try:
        endpoint = _REFERENCE_PATHS[stream]
    except KeyError as exc:
        raise ValueError(f"unsupported Bybit reference stream: {stream}") from exc

    safe_limit = max(1, min(int(page_limit), 1000))
    duration = _TIMEFRAME_DURATION[timeframe]
    cursor_end_ms = int(end_at.timestamp() * 1000)
    collected: dict[datetime, HistoricalReferenceBar] = {}
    reports: list[FetchReport] = []

    while True:
        url = (
            f"{BASE}/v5/market/{endpoint}?category={category}&symbol={normalized}"
            f"&interval={INTERVALS[timeframe]}&limit={safe_limit}&end={cursor_end_ms}"
        )
        payload, report = fetch(url)
        reports.append(report)
        raw_rows = _result(payload).get("list") or []
        parsed: list[HistoricalReferenceBar] = []
        for raw in raw_rows:
            if not isinstance(raw, list) or len(raw) < 5:
                continue
            open_time = _millis(raw[0])
            values = tuple(_decimal(raw[index]) for index in range(1, 5))
            if open_time is None or any(value is None for value in values):
                continue
            open_price, high, low, close = values
            if open_price is None or high is None or low is None or close is None:
                continue
            parsed.append(
                HistoricalReferenceBar(
                    stream=stream,
                    open_time=open_time,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    tradable_at=open_time + duration,
                )
            )
        if not parsed:
            break
        for row in parsed:
            if start_at <= row.open_time < end_at and row.tradable_at <= end_at:
                collected[row.open_time] = row
        oldest = min(row.open_time for row in parsed)
        if oldest <= start_at:
            break
        next_cursor = int(oldest.timestamp() * 1000) - 1
        if next_cursor >= cursor_end_ms:
            raise RuntimeError("Bybit reference-history cursor did not move backward")
        cursor_end_ms = next_cursor

    return [collected[key] for key in sorted(collected)], tuple(reports)


def historical_funding(
    symbol: str,
    *,
    start_at: datetime,
    end_at: datetime,
    page_limit: int = 200,
    category: str = "linear",
    fetch=http_json,
) -> tuple[list[FundingObservation], tuple[FetchReport, ...]]:
    """Read settled funding prints in ``[start_at, end_at)`` by paging backward."""

    _bounds(start_at, end_at)
    normalized = _symbol(symbol)
    safe_limit = max(1, min(int(page_limit), 200))
    cursor_end_ms = int(end_at.timestamp() * 1000)
    collected: dict[datetime, FundingObservation] = {}
    reports: list[FetchReport] = []

    while True:
        url = (
            f"{BASE}/v5/market/funding/history?category={category}&symbol={normalized}"
            f"&limit={safe_limit}&endTime={cursor_end_ms}"
        )
        payload, report = fetch(url)
        reports.append(report)
        rows = _result(payload).get("list") or []
        parsed: list[FundingObservation] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            rate = _decimal(raw.get("fundingRate"))
            settled_at = _millis(raw.get("fundingRateTimestamp"))
            if rate is None or settled_at is None:
                continue
            parsed.append(
                FundingObservation(
                    rate=rate,
                    settled_at=settled_at,
                    tradable_at=settled_at,
                    source="bybit-v5-funding-history",
                )
            )
        if not parsed:
            break
        for row in parsed:
            if start_at <= row.settled_at < end_at:
                collected[row.settled_at] = row
        oldest = min(row.settled_at for row in parsed)
        if oldest <= start_at:
            break
        next_cursor = int(oldest.timestamp() * 1000) - 1
        if next_cursor >= cursor_end_ms:
            raise RuntimeError("Bybit funding-history cursor did not move backward")
        cursor_end_ms = next_cursor

    return [collected[key] for key in sorted(collected)], tuple(reports)


def historical_open_interest(
    symbol: str,
    *,
    interval: str,
    start_at: datetime,
    end_at: datetime,
    page_limit: int = 200,
    category: str = "linear",
    fetch=http_json,
) -> tuple[list[HistoricalOpenInterest], tuple[FetchReport, ...]]:
    """Read cursor-paged open interest bounded to the historical decision window."""

    _bounds(start_at, end_at)
    normalized = _symbol(symbol)
    normalized_interval = interval.strip()
    if not normalized_interval:
        raise ValueError("open-interest interval must not be blank")
    safe_limit = max(1, min(int(page_limit), 200))
    cursor = ""
    seen_cursors: set[str] = set()
    collected: dict[datetime, HistoricalOpenInterest] = {}
    reports: list[FetchReport] = []

    while True:
        url = (
            f"{BASE}/v5/market/open-interest?category={category}&symbol={normalized}"
            f"&intervalTime={normalized_interval}&limit={safe_limit}"
            f"&startTime={int(start_at.timestamp() * 1000)}"
            f"&endTime={int(end_at.timestamp() * 1000)}"
        )
        if cursor:
            url += f"&cursor={quote(cursor, safe='')}"
        payload, report = fetch(url)
        reports.append(report)
        result = _result(payload)
        rows = result.get("list") or []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            observed_at = _millis(raw.get("timestamp"))
            value = _decimal(raw.get("openInterest"))
            if observed_at is None or value is None or value < 0:
                continue
            if start_at <= observed_at < end_at:
                collected[observed_at] = HistoricalOpenInterest(
                    observed_at=observed_at,
                    open_interest=value,
                    tradable_at=observed_at,
                )
        next_cursor = str(result.get("nextPageCursor") or "").strip()
        if not next_cursor:
            break
        if next_cursor == cursor or next_cursor in seen_cursors:
            raise RuntimeError("Bybit open-interest cursor did not advance")
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return [collected[key] for key in sorted(collected)], tuple(reports)


__all__ = [
    "HistoricalOpenInterest",
    "HistoricalReferenceBar",
    "historical_funding",
    "historical_klines",
    "historical_open_interest",
    "historical_reference_klines",
]
