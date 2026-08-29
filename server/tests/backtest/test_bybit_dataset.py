from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.backtest.bybit_dataset import build_bybit_history_snapshot
from app.backtest.bybit_history import HistoricalOpenInterest, HistoricalReferenceBar
from app.market.candles import Candle
from app.market.derivatives import FundingObservation
from app.models.enums import Timeframe


START = datetime(2026, 8, 1, 0, tzinfo=UTC)
END = datetime(2026, 8, 2, 0, tzinfo=UTC)


def _candle(at: datetime, value: str, source: str = "bybit-history") -> Candle:
    price = Decimal(value)
    return Candle(
        open_time=at,
        open=price,
        high=price,
        low=price,
        close=price,
        volume_units=Decimal("10"),
        volume_notional=Decimal("1000"),
        is_closed=True,
        source=source,
    )


def _reference(stream: str, at: datetime, value: str) -> HistoricalReferenceBar:
    price = Decimal(value)
    return HistoricalReferenceBar(
        stream=stream,
        open_time=at,
        open=price,
        high=price,
        low=price,
        close=price,
        tradable_at=datetime(2026, 8, 1, 1, tzinfo=UTC),
    )


def test_bybit_snapshot_contains_all_required_streams_with_exact_tradable_times() -> None:
    h1 = _candle(START, "100")
    d1 = _candle(START, "100")
    mark = _reference("mark", START, "101")
    index = _reference("index", START, "99")
    premium = _reference("premium", START, "2")
    oi = HistoricalOpenInterest(
        observed_at=datetime(2026, 8, 1, 1, tzinfo=UTC),
        open_interest=Decimal("500"),
        tradable_at=datetime(2026, 8, 1, 1, tzinfo=UTC),
    )
    funding = FundingObservation(
        rate=Decimal("0.0001"),
        settled_at=datetime(2026, 8, 1, 8, tzinfo=UTC),
        tradable_at=datetime(2026, 8, 1, 8, tzinfo=UTC),
        source="bybit-v5-funding-history",
    )

    manifest = build_bybit_history_snapshot(
        symbol="BTCUSDT",
        start_at=START,
        end_at=END,
        trade_h1=[h1],
        trade_d1=[d1],
        mark_h1=[mark],
        index_h1=[index],
        premium_h1=[premium],
        open_interest=[oi],
        funding=[funding],
        source_watermark={"provider": "fixture", "request_end": END.isoformat()},
    )

    streams = [row.values["stream"] for row in manifest.rows]
    assert sorted(streams) == ["funding", "index_h1", "mark_h1", "open_interest", "premium_h1", "trade_d1", "trade_h1"]
    by_stream = {row.values["stream"]: row for row in manifest.rows}
    assert by_stream["trade_h1"].tradable_at == datetime(2026, 8, 1, 1, tzinfo=UTC)
    assert by_stream["trade_d1"].tradable_at == END
    assert by_stream["funding"].tradable_at == funding.settled_at
    assert manifest.dataset_name == "bybit_history:BTCUSDT"
    assert manifest.tradable_at == END
    assert manifest.row_count == 7


def test_bybit_snapshot_is_deterministic_and_rejects_wrong_reference_stream() -> None:
    kwargs = dict(
        symbol="BTCUSDT",
        start_at=START,
        end_at=END,
        trade_h1=[_candle(START, "100")],
        trade_d1=[_candle(START, "100")],
        mark_h1=[_reference("mark", START, "101")],
        index_h1=[_reference("index", START, "99")],
        premium_h1=[_reference("premium", START, "2")],
        open_interest=[],
        funding=[],
        source_watermark={"provider": "fixture"},
    )
    first = build_bybit_history_snapshot(**kwargs)
    second = build_bybit_history_snapshot(**kwargs)
    assert first.snapshot_id == second.snapshot_id
    assert first.content_sha256 == second.content_sha256

    bad = dict(kwargs)
    bad["mark_h1"] = [_reference("index", START, "101")]
    try:
        build_bybit_history_snapshot(**bad)
    except ValueError:
        pass
    else:
        raise AssertionError("wrong reference stream must fail closed")


def test_bybit_snapshot_rejects_unclosed_trade_candle_and_future_tradable_fact() -> None:
    open_candle = Candle(
        open_time=START,
        open=Decimal("100"),
        high=Decimal("100"),
        low=Decimal("100"),
        close=Decimal("100"),
        is_closed=False,
        source="fixture",
    )
    try:
        build_bybit_history_snapshot(
            symbol="BTCUSDT",
            start_at=START,
            end_at=END,
            trade_h1=[open_candle],
            trade_d1=[],
            mark_h1=[],
            index_h1=[],
            premium_h1=[],
            open_interest=[],
            funding=[],
            source_watermark={},
        )
    except ValueError:
        pass
    else:
        raise AssertionError("open trade candle must fail closed")

    future = HistoricalOpenInterest(
        observed_at=END,
        open_interest=Decimal("1"),
        tradable_at=datetime(2026, 8, 2, 1, tzinfo=UTC),
    )
    try:
        build_bybit_history_snapshot(
            symbol="BTCUSDT",
            start_at=START,
            end_at=END,
            trade_h1=[],
            trade_d1=[],
            mark_h1=[],
            index_h1=[],
            premium_h1=[],
            open_interest=[future],
            funding=[],
            source_watermark={},
        )
    except ValueError:
        pass
    else:
        raise AssertionError("future-tradable fact must fail closed")
