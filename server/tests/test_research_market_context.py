"""Deterministic D1 timing overlay for early equity hypotheses."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.market.candles import Candle
from app.models.enums import ResearchDirection
from app.research.market_context import MIN_BARS, evaluate

D = Decimal
START = datetime(2026, 1, 1, tzinfo=UTC)


def candles(
    closes: list[Decimal],
    *,
    last_volume: Decimal = D(100),
) -> list[Candle]:
    result: list[Candle] = []
    previous = closes[0]
    for index, close in enumerate(closes):
        result.append(
            Candle(
                open_time=START + timedelta(days=index),
                open=previous,
                high=max(previous, close) + D("0.4"),
                low=min(previous, close) - D("0.4"),
                close=close,
                volume_units=(
                    last_volume if index == len(closes) - 1 else D(100)
                ),
            )
        )
        previous = close
    return result


def test_positive_trend_pullback_is_aligned_entry():
    closes = [D(100) + D("0.3") * index for index in range(95)]
    closes += [D("128"), D("127.5"), D("127"), D("126.7"), D("126.5")]

    result = evaluate(
        candles(closes),
        instrument_id="MOEX:EQ:TEST",
        direction=ResearchDirection.POSITIVE,
    )

    assert result.state == "aligned_entry"
    assert result.score is not None and result.score >= D("0.75")
    assert D(result.detail["ema20_ema50_gap_atr"]) > 0
    assert D("-0.35") <= D(result.detail["distance_from_ema20_atr"]) <= D("0.85")


def test_positive_breakout_needs_and_records_relative_volume():
    closes = [D(100) + D("0.1") * index for index in range(99)] + [D("110.4")]

    result = evaluate(
        candles(closes, last_volume=D(200)),
        instrument_id="MOEX:EQ:TEST",
        direction=ResearchDirection.POSITIVE,
    )

    assert result.state == "aligned_entry"
    assert result.detail["directional_breakout"] is True
    assert result.detail["volume_confirmed"] is True
    assert D(result.detail["relative_volume_20d"]) == D(2)


def test_overextended_move_is_capped_instead_of_called_an_entry():
    closes = [D(100) + D("0.1") * index for index in range(99)] + [D(115)]

    result = evaluate(
        candles(closes),
        instrument_id="MOEX:EQ:TEST",
        direction=ResearchDirection.POSITIVE,
    )

    assert result.state == "extended"
    assert result.score is not None and result.score <= D("0.45")
    assert D(result.detail["distance_from_ema20_atr"]) > D(2)


def test_opposing_trend_is_conflicting_and_score_is_capped():
    closes = [D(120) - D("0.1") * index for index in range(100)]

    result = evaluate(
        candles(closes),
        instrument_id="MOEX:EQ:TEST",
        direction=ResearchDirection.POSITIVE,
    )

    assert result.state == "conflicting"
    assert result.score is not None and result.score <= D("0.25")
    assert D(result.detail["ema20_ema50_gap_atr"]) < 0


def test_negative_hypothesis_mirrors_breakdown_logic():
    closes = [D(120) - D("0.1") * index for index in range(99)] + [D("109.6")]

    result = evaluate(
        candles(closes, last_volume=D(200)),
        instrument_id="MOEX:EQ:TEST",
        direction=ResearchDirection.NEGATIVE,
    )

    assert result.state == "aligned_entry"
    assert result.detail["directional_breakout"] is True
    assert result.detail["volume_confirmed"] is True
    assert D(result.detail["ema20_ema50_gap_atr"]) > 0


def test_less_than_eighty_closed_bars_is_insufficient_history():
    history = candles([D(100) + D("0.1") * index for index in range(MIN_BARS)])
    history[-1] = Candle(
        open_time=history[-1].open_time,
        open=history[-1].open,
        high=history[-1].high,
        low=history[-1].low,
        close=history[-1].close,
        volume_units=history[-1].volume_units,
        is_closed=False,
    )

    result = evaluate(
        history,
        instrument_id="MOEX:EQ:TEST",
        direction=ResearchDirection.POSITIVE,
    )

    assert result.state == "insufficient_history"
    assert result.score is None
    assert result.detail["bar_count"] == MIN_BARS - 1
    assert result.detail["reason"] == "not_enough_closed_daily_bars"


def test_flat_market_is_watch_and_output_is_not_a_trade_instruction():
    result = evaluate(
        candles([D(100)] * MIN_BARS),
        instrument_id="MOEX:EQ:TEST",
        direction=ResearchDirection.POSITIVE,
    )

    # ATR is non-zero because each bar has a high/low range, while both EMAs
    # are equal: a watch state is more honest than inventing trend alignment.
    assert result.state == "aligned_watch"
    assert result.detail["not_a_trade_signal"] is True
    assert {"side", "entry", "quantity"}.isdisjoint(result.detail)
