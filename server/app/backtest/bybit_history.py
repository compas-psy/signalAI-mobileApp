"""Research-only deep historical Bybit readers.

The functions in this module are deliberately outside the live market-ingestion
path. They read public Bybit v5 history for immutable research datasets and
never write canonical ``bars``, create ``TradeIdea`` rows or mutate runtime
strategy state.

Every reader is point-in-time bounded. Provider overlap is deduplicated by the
observation timestamp and pagination must make forward/backward progress or the
reader fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from ..market.candles import Candle
from ..market.crypto import BASE, INTERVALS, _parse_kline_rows, _result
from ..market.http import FetchReport, http_json
from ..models.enums import Timeframe

_TIMEFRAME_DURATION: dict[Timeframe, timedelta] = {
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
}

_OI_INTERVALS = frozenset({"5min", "15min", "30min", "1h", "4h", "1d"})
_RATIO_PERIODS = _OI_INTERVALS


@dataclass(frozen=True, slots=True)
class HistoricalObservation:
    """One immutable historical fact with an explicit PIT availability time."""

    observed_at: datetime
    tradable_at: datetime
    values: dict[str, Any]

    def __post_init__(self) -> None:
        _aware(self.observed_at, "observed_at")
        _aware(self.tradable_at, "tradable_at")
        if self.tradable_at < self.observed_at:
            raise ValueError("tradable_at must not precede observed_at")
        if not isinstance(self.values, dict) or not self.values:
            raise ValueError("historical observation values must not be empty")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _bounds(symbol: str, start_at: datetime, end_at: datetime) -> str:
    _aware(start_at, "start_at")
    _aware(end_at, "end_at")
    if end_at <= start_at:
        raise ValueError("end_at must be after start_at")
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("symbol must not be blank")
    return normalized


def _timestamp(value: object) -> datetime:
    try:
        milliseconds = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid Bybit timestamp") from exc
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)


def _decimal(value: object, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid Bybit decimal for {name}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite Bybit decimal for {name}")
    return result


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
    """Read closed traded-price candles in ``[start_at, end_at)`` backwards."""

    normalized = _bounds(symbol, start_at, end_at)
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


def _historical_reference_klines(
    symbol: str,
    timeframe: Timeframe,
    *,
    endpoint: str,
    stream: str,
    start_at: datetime,
    end_at: datetime,
    page_limit: int = 1000,
    category: str = "linear",
    fetch=http_json,
) -> tuple[list[HistoricalObservation], tuple[FetchReport, ...]]:
    """Read mark/index/premium OHLC history using the common v5 kline shape."""

    normalized = _bounds(symbol, start_at, end_at)
    if timeframe not in INTERVALS:
        raise ValueError(f"unsupported Bybit historical timeframe: {timeframe.value}")
    duration = _TIMEFRAME_DURATION[timeframe]
    safe_limit = max(1, min(int(page_limit), 1000))
    cursor_end_ms = int(end_at.timestamp() * 1000)
    collected: dict[datetime, HistoricalObservation] = {}
    reports: list[FetchReport] = []

    while True:
        url = (
            f"{BASE}{endpoint}?category={category}&symbol={normalized}"
            f"&interval={INTERVALS[timeframe]}&limit={safe_limit}&end={cursor_end_ms}"
        )
        payload, report = fetch(url)
        reports.append(report)
        rows = _result(payload).get("list") or []
        if not rows:
            break

        page_times: list[datetime] = []
        for raw in rows:
            if not isinstance(raw, (list, tuple)) or len(raw) < 5:
                raise ValueError(f"invalid Bybit {stream} kline row")
            at = _timestamp(raw[0])
            page_times.append(at)
            # A reference candle is only a historical fact after its interval
            # has closed. This prevents an end-boundary candle from leaking its
            # still-changing close into replay.
            tradable_at = at + duration
            if start_at <= at < end_at and tradable_at <= end_at:
                collected[at] = HistoricalObservation(
                    observed_at=at,
                    tradable_at=tradable_at,
                    values={
                        "open": _decimal(raw[1], "open"),
                        "high": _decimal(raw[2], "high"),
                        "low": _decimal(raw[3], "low"),
                        "close": _decimal(raw[4], "close"),
                    },
                )

        if not page_times:
            break
        oldest = min(page_times)
        if oldest <= start_at:
            break
        next_cursor = int(oldest.timestamp() * 1000) - 1
        if next_cursor >= cursor_end_ms:
            raise RuntimeError(f"Bybit {stream} historical cursor did not move backward")
        cursor_end_ms = next_cursor

    return [collected[key] for key in sorted(collected)], tuple(reports)


def historical_mark_price_klines(
    symbol: str,
    timeframe: Timeframe,
    *,
    start_at: datetime,
    end_at: datetime,
    page_limit: int = 1000,
    category: str = "linear",
    fetch=http_json,
) -> tuple[list[HistoricalObservation], tuple[FetchReport, ...]]:
    return _historical_reference_klines(
        symbol,
        timeframe,
        endpoint="/v5/market/mark-price-kline",
        stream="mark_price",
        start_at=start_at,
        end_at=end_at,
        page_limit=page_limit,
        category=category,
        fetch=fetch,
    )


def historical_index_price_klines(
    symbol: str,
    timeframe: Timeframe,
    *,
    start_at: datetime,
    end_at: datetime,
    page_limit: int = 1000,
    category: str = "linear",
    fetch=http_json,
) -> tuple[list[HistoricalObservation], tuple[FetchReport, ...]]:
    return _historical_reference_klines(
        symbol,
        timeframe,
        endpoint="/v5/market/index-price-kline",
        stream="index_price",
        start_at=start_at,
        end_at=end_at,
        page_limit=page_limit,
        category=category,
        fetch=fetch,
    )


def historical_premium_index_klines(
    symbol: str,
    timeframe: Timeframe,
    *,
    start_at: datetime,
    end_at: datetime,
    page_limit: int = 1000,
    category: str = "linear",
    fetch=http_json,
) -> tuple[list[HistoricalObservation], tuple[FetchReport, ...]]:
    return _historical_reference_klines(
        symbol,
        timeframe,
        endpoint="/v5/market/premium-index-price-kline",
        stream="premium_index",
        start_at=start_at,
        end_at=end_at,
        page_limit=page_limit,
        category=category,
        fetch=fetch,
    )


def historical_funding(
    symbol: str,
    *,
    start_at: datetime,
    end_at: datetime,
    page_limit: int = 200,
    category: str = "linear",
    fetch=http_json,
) -> tuple[list[HistoricalObservation], tuple[FetchReport, ...]]:
    """Read settled funding backwards using the endpoint's ``endTime`` window."""

    normalized = _bounds(symbol, start_at, end_at)
    safe_limit = max(1, min(int(page_limit), 200))
    cursor_end_ms = int(end_at.timestamp() * 1000)
    start_ms = int(start_at.timestamp() * 1000)
    collected: dict[datetime, HistoricalObservation] = {}
    reports: list[FetchReport] = []

    while True:
        url = (
            f"{BASE}/v5/market/funding/history?category={category}&symbol={normalized}"
            f"&startTime={start_ms}&endTime={cursor_end_ms}&limit={safe_limit}"
        )
        payload, report = fetch(url)
        reports.append(report)
        rows = _result(payload).get("list") or []
        if not rows:
            break

        page_times: list[datetime] = []
        for raw in rows:
            if not isinstance(raw, dict):
                raise ValueError("invalid Bybit funding row")
            at = _timestamp(raw.get("fundingRateTimestamp"))
            page_times.append(at)
            if start_at <= at < end_at:
                collected[at] = HistoricalObservation(
                    observed_at=at,
                    tradable_at=at,
                    values={"funding_rate": _decimal(raw.get("fundingRate"), "fundingRate")},
                )

        if not page_times:
            break
        oldest = min(page_times)
        if oldest <= start_at:
            break
        next_cursor = int(oldest.timestamp() * 1000) - 1
        if next_cursor >= cursor_end_ms:
            raise RuntimeError("Bybit funding historical cursor did not move backward")
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
) -> tuple[list[HistoricalObservation], tuple[FetchReport, ...]]:
    """Read OI with Bybit cursor pagination.

    For linear contracts Bybit reports OI in the base coin, not quote notional.
    The raw value is therefore preserved with an explicit ``unit='base'`` tag;
    callers needing quote notional must combine it with point-in-time price.
    """

    normalized = _bounds(symbol, start_at, end_at)
    if interval not in _OI_INTERVALS:
        raise ValueError(f"unsupported Bybit OI interval: {interval}")
    safe_limit = max(1, min(int(page_limit), 200))
    start_ms = int(start_at.timestamp() * 1000)
    end_ms = int(end_at.timestamp() * 1000)
    cursor = ""
    seen_cursors: set[str] = set()
    collected: dict[datetime, HistoricalObservation] = {}
    reports: list[FetchReport] = []

    while True:
        url = (
            f"{BASE}/v5/market/open-interest?category={category}&symbol={normalized}"
            f"&intervalTime={interval}&startTime={start_ms}&endTime={end_ms}"
            f"&limit={safe_limit}"
        )
        if cursor:
            url += f"&cursor={quote(cursor, safe='')}"
        payload, report = fetch(url)
        reports.append(report)
        result = _result(payload)
        rows = result.get("list") or []
        for raw in rows:
            if not isinstance(raw, dict):
                raise ValueError("invalid Bybit open-interest row")
            at = _timestamp(raw.get("timestamp"))
            if start_at <= at < end_at:
                collected[at] = HistoricalObservation(
                    observed_at=at,
                    tradable_at=at,
                    values={
                        "open_interest": _decimal(raw.get("openInterest"), "openInterest"),
                        "unit": "base" if category == "linear" else "quote",
                    },
                )

        next_cursor = str(result.get("nextPageCursor") or "")
        if not next_cursor:
            break
        if next_cursor == cursor or next_cursor in seen_cursors:
            raise RuntimeError("Bybit open-interest cursor did not progress")
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return [collected[key] for key in sorted(collected)], tuple(reports)


def historical_long_short_ratio(
    symbol: str,
    *,
    period: str,
    start_at: datetime,
    end_at: datetime,
    page_limit: int = 500,
    category: str = "linear",
    fetch=http_json,
) -> tuple[list[HistoricalObservation], tuple[FetchReport, ...]]:
    """Read account long/short ratios with cursor pagination."""

    normalized = _bounds(symbol, start_at, end_at)
    if period not in _RATIO_PERIODS:
        raise ValueError(f"unsupported Bybit account-ratio period: {period}")
    safe_limit = max(1, min(int(page_limit), 500))
    start_ms = int(start_at.timestamp() * 1000)
    end_ms = int(end_at.timestamp() * 1000)
    cursor = ""
    seen_cursors: set[str] = set()
    collected: dict[datetime, HistoricalObservation] = {}
    reports: list[FetchReport] = []

    while True:
        url = (
            f"{BASE}/v5/market/account-ratio?category={category}&symbol={normalized}"
            f"&period={period}&startTime={start_ms}&endTime={end_ms}&limit={safe_limit}"
        )
        if cursor:
            url += f"&cursor={quote(cursor, safe='')}"
        payload, report = fetch(url)
        reports.append(report)
        result = _result(payload)
        rows = result.get("list") or []
        for raw in rows:
            if not isinstance(raw, dict):
                raise ValueError("invalid Bybit long/short row")
            at = _timestamp(raw.get("timestamp"))
            if start_at <= at < end_at:
                buy = _decimal(raw.get("buyRatio"), "buyRatio")
                sell = _decimal(raw.get("sellRatio"), "sellRatio")
                collected[at] = HistoricalObservation(
                    observed_at=at,
                    tradable_at=at,
                    values={
                        "long_ratio": buy,
                        "short_ratio": sell,
                        "long_short_ratio": None if sell == 0 else buy / sell,
                    },
                )

        next_cursor = str(result.get("nextPageCursor") or "")
        if not next_cursor:
            break
        if next_cursor == cursor or next_cursor in seen_cursors:
            raise RuntimeError("Bybit account-ratio cursor did not progress")
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return [collected[key] for key in sorted(collected)], tuple(reports)


__all__ = [
    "HistoricalObservation",
    "historical_funding",
    "historical_index_price_klines",
    "historical_klines",
    "historical_long_short_ratio",
    "historical_mark_price_klines",
    "historical_open_interest",
    "historical_premium_index_klines",
]
