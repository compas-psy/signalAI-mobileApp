from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.bybit_dataset import (
    DATA_BLOCKED,
    DATA_READY,
    REQUIRED_BYBIT_STREAMS,
    build_multistream_manifest,
)
from app.backtest.bybit_history import (
    HistoricalObservation,
    historical_funding,
    historical_open_interest,
)


def _ms(value: datetime) -> str:
    return str(int(value.timestamp() * 1000))


def test_historical_funding_pages_backward_and_deduplicates() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 8, 3, tzinfo=UTC)
    t1 = start + timedelta(hours=8)
    t2 = start + timedelta(hours=16)
    t3 = start + timedelta(hours=24)
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        if len(calls) == 1:
            return {
                "retCode": 0,
                "result": {
                    "list": [
                        {"fundingRate": "0.0003", "fundingRateTimestamp": _ms(t3)},
                        {"fundingRate": "0.0002", "fundingRateTimestamp": _ms(t2)},
                    ]
                },
            }, object()
        return {
            "retCode": 0,
            "result": {
                "list": [
                    {"fundingRate": "0.0002", "fundingRateTimestamp": _ms(t2)},
                    {"fundingRate": "0.0001", "fundingRateTimestamp": _ms(t1)},
                ]
            },
        }, object()

    rows, reports = historical_funding(
        "BTCUSDT", start_at=start, end_at=end, page_limit=2, fetch=fetch
    )

    assert [row.observed_at for row in rows] == [t1, t2, t3]
    assert [row.values["funding_rate"] for row in rows] == [
        Decimal("0.0001"), Decimal("0.0002"), Decimal("0.0003")
    ]
    assert all(row.tradable_at == row.observed_at for row in rows)
    assert len(reports) == 2
    assert f"endTime={int(t2.timestamp() * 1000) - 1}" in calls[1]


def test_historical_open_interest_follows_cursor_and_filters_bounds() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = datetime(2026, 8, 2, tzinfo=UTC)
    t1 = start + timedelta(hours=1)
    t2 = start + timedelta(hours=2)
    outside = end + timedelta(hours=1)
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        if "cursor=" not in url:
            return {
                "retCode": 0,
                "result": {
                    "nextPageCursor": "next-1",
                    "list": [
                        {"openInterest": "100", "timestamp": _ms(t1)},
                        {"openInterest": "999", "timestamp": _ms(outside)},
                    ],
                },
            }, object()
        return {
            "retCode": 0,
            "result": {
                "nextPageCursor": "",
                "list": [
                    {"openInterest": "101", "timestamp": _ms(t2)},
                    {"openInterest": "100", "timestamp": _ms(t1)},
                ],
            },
        }, object()

    rows, reports = historical_open_interest(
        "BTCUSDT",
        interval="1h",
        start_at=start,
        end_at=end,
        page_limit=2,
        fetch=fetch,
    )

    assert [row.observed_at for row in rows] == [t1, t2]
    assert [row.values["open_interest"] for row in rows] == [Decimal("100"), Decimal("101")]
    assert len(reports) == 2
    assert "cursor=next-1" in calls[1]
    assert f"startTime={_ms(start)}" in calls[0]
    assert f"endTime={_ms(end)}" in calls[0]


def _stream(
    name: str,
    *,
    start: datetime,
    end: datetime,
    first_offset: timedelta = timedelta(0),
) -> tuple[HistoricalObservation, ...]:
    first = start + first_offset
    last = end - timedelta(hours=1)
    return (
        HistoricalObservation(
            observed_at=first,
            tradable_at=first,
            values={"stream": name, "value": Decimal("1")},
        ),
        HistoricalObservation(
            observed_at=last,
            tradable_at=last,
            values={"stream": name, "value": Decimal("2")},
        ),
    )


def test_multistream_manifest_is_data_ready_only_when_every_required_stream_covers_36m() -> None:
    start = datetime(2023, 8, 29, tzinfo=UTC)
    end = datetime(2026, 8, 29, tzinfo=UTC)
    streams = {
        name: _stream(name, start=start, end=end)
        for name in REQUIRED_BYBIT_STREAMS
    }

    built = build_multistream_manifest(
        symbol="BTCUSDT",
        start_at=start,
        end_at=end,
        streams=streams,
        min_history_months=36,
    )

    assert built.status == DATA_READY
    assert all(item.ready for item in built.coverage)
    assert built.manifest.dataset_name == "bybit:BTCUSDT:multistream"
    assert built.manifest.source_watermark["readiness"] == DATA_READY
    assert built.manifest.row_count == len(REQUIRED_BYBIT_STREAMS) * 2


def test_multistream_manifest_blocks_when_one_required_stream_is_short() -> None:
    start = datetime(2023, 8, 29, tzinfo=UTC)
    end = datetime(2026, 8, 29, tzinfo=UTC)
    streams = {
        name: _stream(name, start=start, end=end)
        for name in REQUIRED_BYBIT_STREAMS
    }
    streams["open_interest"] = _stream(
        "open_interest",
        start=start,
        end=end,
        first_offset=timedelta(days=45),
    )

    built = build_multistream_manifest(
        symbol="BTCUSDT",
        start_at=start,
        end_at=end,
        streams=streams,
        min_history_months=36,
    )

    assert built.status == DATA_BLOCKED
    oi = next(item for item in built.coverage if item.stream == "open_interest")
    assert oi.ready is False
    assert oi.reason == "HISTORY_LT_36M"
    assert built.manifest.source_watermark["readiness"] == DATA_BLOCKED


def test_multistream_manifest_identity_is_content_addressed() -> None:
    start = datetime(2023, 8, 29, tzinfo=UTC)
    end = datetime(2026, 8, 29, tzinfo=UTC)
    streams = {
        name: _stream(name, start=start, end=end)
        for name in REQUIRED_BYBIT_STREAMS
    }

    first = build_multistream_manifest(
        symbol="BTCUSDT", start_at=start, end_at=end, streams=streams
    )
    second = build_multistream_manifest(
        symbol="BTCUSDT", start_at=start, end_at=end, streams=dict(reversed(list(streams.items())))
    )

    assert first.manifest.snapshot_id == second.manifest.snapshot_id
    assert first.manifest.content_sha256 == second.manifest.content_sha256
