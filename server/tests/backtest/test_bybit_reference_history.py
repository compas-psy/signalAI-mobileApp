from __future__ import annotations

from datetime import UTC, datetime

from app.backtest.bybit_history import (
    historical_funding,
    historical_open_interest,
    historical_reference_klines,
)
from app.models.enums import Timeframe


def _ms(value: datetime) -> str:
    return str(int(value.timestamp() * 1000))


def _price_row(at: datetime, value: str) -> list[str]:
    return [_ms(at), value, value, value, value]


def test_reference_klines_page_backward_and_preserve_stream_identity() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 8, 4, tzinfo=UTC)
    aug1 = datetime(2026, 8, 1, tzinfo=UTC)
    aug2 = datetime(2026, 8, 2, tzinfo=UTC)
    aug3 = datetime(2026, 8, 3, tzinfo=UTC)
    before = datetime(2026, 7, 31, tzinfo=UTC)
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        if len(calls) == 1:
            return {
                "retCode": 0,
                "result": {"list": [_price_row(end, "104"), _price_row(aug3, "103"), _price_row(aug2, "102")]},
            }, object()
        return {
            "retCode": 0,
            "result": {"list": [_price_row(aug2, "102"), _price_row(aug1, "101"), _price_row(before, "99")]},
        }, object()

    rows, reports = historical_reference_klines(
        "BTCUSDT",
        Timeframe.D1,
        stream="mark",
        start_at=start,
        end_at=end,
        page_limit=3,
        fetch=fetch,
    )

    assert [row.open_time for row in rows] == [aug1, aug2, aug3]
    assert [row.close for row in rows] == [101, 102, 103]
    assert all(row.stream == "mark" for row in rows)
    assert len(reports) == 2
    assert "/v5/market/mark-price-kline?" in calls[0]
    assert f"end={_ms(end)}" in calls[0]
    assert f"end={int(aug2.timestamp() * 1000) - 1}" in calls[1]


def test_reference_stream_names_are_explicit_and_unknown_stream_fails_closed() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 8, 2, tzinfo=UTC)
    for stream, path in (
        ("mark", "mark-price-kline"),
        ("index", "index-price-kline"),
        ("premium", "premium-index-price-kline"),
    ):
        calls: list[str] = []
        historical_reference_klines(
            "BTCUSDT",
            Timeframe.H1,
            stream=stream,
            start_at=start,
            end_at=end,
            fetch=lambda url, calls=calls: (calls.append(url) or {"retCode": 0, "result": {"list": []}}, object()),
        )
        assert f"/v5/market/{path}?" in calls[0]

    try:
        historical_reference_klines(
            "BTCUSDT", Timeframe.H1, stream="ticker", start_at=start, end_at=end,
            fetch=lambda _: ({}, object()),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("unknown historical reference stream must fail closed")


def test_historical_funding_pages_backward_without_future_settlements() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 8, 3, tzinfo=UTC)
    aug1 = datetime(2026, 8, 1, 8, tzinfo=UTC)
    aug2 = datetime(2026, 8, 2, 8, tzinfo=UTC)
    future = datetime(2026, 8, 3, 8, tzinfo=UTC)
    before = datetime(2026, 7, 31, 8, tzinfo=UTC)
    calls: list[str] = []

    def item(at: datetime, rate: str) -> dict[str, str]:
        return {"fundingRate": rate, "fundingRateTimestamp": _ms(at)}

    def fetch(url: str):
        calls.append(url)
        if len(calls) == 1:
            return {"retCode": 0, "result": {"list": [item(future, "0.003"), item(aug2, "0.002")]}}, object()
        return {"retCode": 0, "result": {"list": [item(aug2, "0.002"), item(aug1, "0.001"), item(before, "0.0005")]}}, object()

    rows, reports = historical_funding(
        "BTCUSDT", start_at=start, end_at=end, page_limit=2, fetch=fetch
    )

    assert [row.settled_at for row in rows] == [aug1, aug2]
    assert len(reports) == 2
    assert f"endTime={_ms(end)}" in calls[0]
    assert f"endTime={int(aug2.timestamp() * 1000) - 1}" in calls[1]


def test_historical_open_interest_follows_cursor_and_bounds_rows() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 8, 3, tzinfo=UTC)
    aug1 = datetime(2026, 8, 1, 1, tzinfo=UTC)
    aug2 = datetime(2026, 8, 2, 1, tzinfo=UTC)
    future = datetime(2026, 8, 3, 1, tzinfo=UTC)
    calls: list[str] = []

    def item(at: datetime, oi: str) -> dict[str, str]:
        return {"timestamp": _ms(at), "openInterest": oi}

    def fetch(url: str):
        calls.append(url)
        if "cursor=" not in url:
            return {
                "retCode": 0,
                "result": {"list": [item(future, "300"), item(aug2, "200")], "nextPageCursor": "next-1"},
            }, object()
        return {
            "retCode": 0,
            "result": {"list": [item(aug2, "200"), item(aug1, "100")], "nextPageCursor": ""},
        }, object()

    rows, reports = historical_open_interest(
        "BTCUSDT",
        interval="1h",
        start_at=start,
        end_at=end,
        page_limit=2,
        fetch=fetch,
    )

    assert [row.observed_at for row in rows] == [aug1, aug2]
    assert [row.open_interest for row in rows] == [100, 200]
    assert len(reports) == 2
    assert "intervalTime=1h" in calls[0]
    assert "cursor=next-1" in calls[1]
