"""Research-only deep historical Bybit candle reader.

This module is deliberately outside the live market-ingestion path.  It pages
backward through public Bybit kline history for immutable backtest datasets and
never writes canonical ``bars``, creates ``TradeIdea`` rows or changes runtime
scanner behaviour.
"""

from __future__ import annotations

from datetime import datetime, timedelta

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


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


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
    """Read closed candles in ``[start_at, end_at)`` using backward paging.

    Bybit returns newest-first pages.  Each subsequent request therefore moves
    its inclusive ``end`` cursor one millisecond before the oldest timestamp
    observed on the previous page. Provider overlap is deduplicated by exact
    candle open time. Missing/invalid bounds fail closed instead of silently
    shortening a backtest.
    """

    _aware(start_at, "start_at")
    _aware(end_at, "end_at")
    if end_at <= start_at:
        raise ValueError("end_at must be after start_at")
    normalized = symbol.strip().upper()
    if not normalized:
        raise ValueError("symbol must not be blank")
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


__all__ = ["historical_klines"]
