from __future__ import annotations

from datetime import UTC, datetime

from app.market.crypto import historical_klines
from app.models.enums import Timeframe


def _ms(value: datetime) -> str:
    return str(int(value.timestamp() * 1000))


def _row(at: datetime, close: str) -> list[str]:
    return [_ms(at), close, close, close, close, "10", "100"]


def test_historical_klines_pages_backward_without_duplicates_or_boundary_leakage() -> None:
    start = datetime(2026, 7, 30, tzinfo=UTC)
    end = datetime(2026, 8, 4, tzinfo=UTC)
    aug1 = datetime(2026, 8, 1, tzinfo=UTC)
    aug2 = datetime(2026, 8, 2, tzinfo=UTC)
    aug3 = datetime(2026, 8, 3, tzinfo=UTC)
    jul30 = datetime(2026, 7, 30, tzinfo=UTC)
    jul31 = datetime(2026, 7, 31, tzinfo=UTC)
    before = datetime(2026, 7, 29, tzinfo=UTC)
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        if len(calls) == 1:
            # Bybit returns newest -> oldest and may include the end boundary.
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        _row(end, "104"),
                        _row(aug3, "103"),
                        _row(aug2, "102"),
                        _row(aug1, "101"),
                    ]
                },
            }, object()
        return {
            "retCode": 0,
            "result": {
                "list": [
                    _row(aug1, "101"),  # overlapping provider row: dedupe it
                    _row(jul31, "99"),
                    _row(jul30, "98"),
                    _row(before, "97"),
                ]
            },
        }, object()

    candles, reports = historical_klines(
        "BTCUSDT",
        Timeframe.D1,
        start_at=start,
        end_at=end,
        page_limit=4,
        fetch=fetch,
    )

    assert [item.open_time for item in candles] == [jul30, jul31, aug1, aug2, aug3]
    assert all(item.is_closed for item in candles)
    assert len({item.open_time for item in candles}) == len(candles)
    assert len(reports) == 2
    assert len(calls) == 2
    assert "limit=4" in calls[0]
    assert f"end={_ms(end)}" in calls[0]
    assert f"end={int(aug1.timestamp() * 1000) - 1}" in calls[1]


def test_historical_klines_fails_closed_on_naive_or_reversed_bounds() -> None:
    aware = datetime(2026, 8, 4, tzinfo=UTC)
    naive = datetime(2026, 8, 1)

    for start_at, end_at in (
        (naive, aware),
        (aware, naive),
        (aware, aware),
    ):
        try:
            historical_klines(
                "BTCUSDT",
                Timeframe.D1,
                start_at=start_at,
                end_at=end_at,
                fetch=lambda _: ({}, object()),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid historical bounds must fail closed")
