from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.market.candles import Candle
from app.models.enums import Direction, QualityFlag
from app.strategies.breakout_v2 import BreakoutMarketFacts, evaluate_breakout_v2
from app.strategies.result_v2 import DataQualityState, StrategyResultV2


EVALUATED_AT = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)


def _range_bars(*, count: int = 30) -> list[Candle]:
    first = EVALUATED_AT - timedelta(hours=count + 1)
    bars: list[Candle] = []
    for index in range(count):
        close = Decimal("100.2") if index % 2 == 0 else Decimal("99.8")
        bars.append(
            Candle(
                open_time=first + timedelta(hours=index),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=close,
                volume_units=Decimal("1000"),
                is_closed=True,
                source="fixture",
            )
        )
    return bars


def _confirmed_breakout(direction: Direction = Direction.LONG) -> list[Candle]:
    bars = _range_bars()
    if direction is Direction.LONG:
        bars.extend(
            [
                Candle(
                    open_time=EVALUATED_AT - timedelta(hours=1),
                    open=Decimal("100.5"),
                    high=Decimal("102.5"),
                    low=Decimal("100.4"),
                    close=Decimal("102.2"),
                    volume_units=Decimal("1800"),
                    is_closed=True,
                    source="fixture",
                ),
                Candle(
                    open_time=EVALUATED_AT,
                    open=Decimal("102.0"),
                    high=Decimal("103.6"),
                    low=Decimal("101.8"),
                    close=Decimal("103.3"),
                    volume_units=Decimal("1700"),
                    is_closed=True,
                    source="fixture",
                ),
            ]
        )
    else:
        bars.extend(
            [
                Candle(
                    open_time=EVALUATED_AT - timedelta(hours=1),
                    open=Decimal("99.5"),
                    high=Decimal("99.6"),
                    low=Decimal("97.5"),
                    close=Decimal("97.8"),
                    volume_units=Decimal("1800"),
                    is_closed=True,
                    source="fixture",
                ),
                Candle(
                    open_time=EVALUATED_AT,
                    open=Decimal("98.0"),
                    high=Decimal("98.2"),
                    low=Decimal("96.4"),
                    close=Decimal("96.7"),
                    volume_units=Decimal("1700"),
                    is_closed=True,
                    source="fixture",
                ),
            ]
        )
    return bars


def _facts(
    *,
    spread_bps: Decimal = Decimal("4"),
    round_trip_cost_bps: Decimal = Decimal("15"),
    regime: str = "TREND",
) -> BreakoutMarketFacts:
    return BreakoutMarketFacts(
        spread_bps=spread_bps,
        round_trip_cost_bps=round_trip_cost_bps,
        regime=regime,
        observed_at=EVALUATED_AT,
        tradable_at=EVALUATED_AT,
        source="fixture-market-facts",
    )


def test_breakout_v2_emits_long_candidate_only_after_two_closed_confirmations() -> None:
    result = evaluate_breakout_v2(
        instrument_id="CRYPTO:BTCUSDT",
        bars=_confirmed_breakout(Direction.LONG),
        market=_facts(),
        evaluated_at=EVALUATED_AT,
    )

    assert isinstance(result, StrategyResultV2)
    assert result.strategy_family == "BREAKOUT"
    assert result.strategy_version == "breakout_v2"
    assert result.direction is Direction.LONG
    assert result.raw_edge_score > 0
    assert result.entry_hypothesis.kind == "BREAKOUT_CONTINUATION"
    assert result.entry_hypothesis.reference == Decimal("103.3")
    assert result.data_quality_state is DataQualityState.GOOD
    assert result.regime_compatibility == ("TREND", "NORMAL_VOL", "HIGH_VOL")
    assert {feature.name for feature in result.feature_provenance} >= {
        "breakout_level",
        "first_confirmation_atr",
        "second_confirmation_atr",
        "close_location",
        "atr_move_bps",
        "spread_bps",
        "round_trip_cost_bps",
        "regime",
    }
    assert all(feature.tradable_at <= result.evaluated_at for feature in result.feature_provenance)
    assert not hasattr(result, "stop")
    assert not hasattr(result, "targets")
    assert not hasattr(result, "order_intent")


def test_breakout_v2_emits_short_candidate_symmetrically() -> None:
    result = evaluate_breakout_v2(
        instrument_id="CRYPTO:ETHUSDT",
        bars=_confirmed_breakout(Direction.SHORT),
        market=_facts(regime="HIGH_VOL"),
        evaluated_at=EVALUATED_AT,
    )

    assert isinstance(result, StrategyResultV2)
    assert result.direction is Direction.SHORT
    assert result.raw_edge_score > 0
    assert "below" in result.entry_hypothesis.rationale


def test_breakout_v2_rejects_single_spike_without_persistent_confirmation() -> None:
    bars = _confirmed_breakout(Direction.LONG)
    bars[-2] = Candle(
        open_time=bars[-2].open_time,
        open=Decimal("100.2"),
        high=Decimal("102.5"),
        low=Decimal("99.8"),
        close=Decimal("100.7"),
        volume_units=Decimal("1800"),
        is_closed=True,
        source="fixture",
    )

    result = evaluate_breakout_v2(
        instrument_id="CRYPTO:BTCUSDT",
        bars=bars,
        market=_facts(),
        evaluated_at=EVALUATED_AT,
    )

    assert result is None


def test_breakout_v2_rejects_confirmation_that_closes_back_inside_range() -> None:
    bars = _confirmed_breakout(Direction.LONG)
    bars[-1] = Candle(
        open_time=bars[-1].open_time,
        open=Decimal("102.0"),
        high=Decimal("103.2"),
        low=Decimal("99.6"),
        close=Decimal("100.4"),
        volume_units=Decimal("1700"),
        is_closed=True,
        source="fixture",
    )

    assert evaluate_breakout_v2(
        instrument_id="CRYPTO:BTCUSDT",
        bars=bars,
        market=_facts(),
        evaluated_at=EVALUATED_AT,
    ) is None


def test_breakout_v2_rejects_wide_spread_even_when_price_breaks_out() -> None:
    result = evaluate_breakout_v2(
        instrument_id="CRYPTO:BTCUSDT",
        bars=_confirmed_breakout(),
        market=_facts(spread_bps=Decimal("25")),
        evaluated_at=EVALUATED_AT,
    )

    assert result is None


def test_breakout_v2_requires_atr_move_to_cover_round_trip_costs_with_margin() -> None:
    result = evaluate_breakout_v2(
        instrument_id="CRYPTO:BTCUSDT",
        bars=_confirmed_breakout(),
        market=_facts(round_trip_cost_bps=Decimal("80")),
        evaluated_at=EVALUATED_AT,
    )

    assert result is None


def test_breakout_v2_rejects_range_regime() -> None:
    result = evaluate_breakout_v2(
        instrument_id="CRYPTO:BTCUSDT",
        bars=_confirmed_breakout(),
        market=_facts(regime="RANGE"),
        evaluated_at=EVALUATED_AT,
    )

    assert result is None


def test_breakout_v2_ignores_forming_and_future_bars_no_lookahead() -> None:
    bars = _confirmed_breakout()
    baseline = evaluate_breakout_v2(
        instrument_id="CRYPTO:BTCUSDT",
        bars=bars,
        market=_facts(),
        evaluated_at=EVALUATED_AT,
    )
    future = Candle(
        open_time=EVALUATED_AT + timedelta(hours=1),
        open=Decimal("103"),
        high=Decimal("150"),
        low=Decimal("80"),
        close=Decimal("149"),
        volume_units=Decimal("999999"),
        is_closed=True,
        source="future-fixture",
    )
    forming = Candle(
        open_time=EVALUATED_AT,
        open=Decimal("103"),
        high=Decimal("160"),
        low=Decimal("70"),
        close=Decimal("159"),
        volume_units=Decimal("999999"),
        is_closed=False,
        source="forming-fixture",
    )

    with_invisible = evaluate_breakout_v2(
        instrument_id="CRYPTO:BTCUSDT",
        bars=[*bars, forming, future],
        market=_facts(),
        evaluated_at=EVALUATED_AT,
    )

    assert isinstance(baseline, StrategyResultV2)
    assert with_invisible == baseline


def test_breakout_v2_rejects_market_fact_that_was_not_tradable_yet() -> None:
    market = replace(
        _facts(),
        tradable_at=EVALUATED_AT + timedelta(minutes=1),
    )

    result = evaluate_breakout_v2(
        instrument_id="CRYPTO:BTCUSDT",
        bars=_confirmed_breakout(),
        market=market,
        evaluated_at=EVALUATED_AT,
    )

    assert result is None


def test_breakout_v2_preserves_blocked_bar_quality_as_evidence() -> None:
    bars = _confirmed_breakout()
    bars[-1] = replace(bars[-1], quality_flags=(QualityFlag.STALE.value,))

    result = evaluate_breakout_v2(
        instrument_id="CRYPTO:BTCUSDT",
        bars=bars,
        market=_facts(),
        evaluated_at=EVALUATED_AT,
    )

    assert isinstance(result, StrategyResultV2)
    assert result.data_quality_state is DataQualityState.BLOCKED
    assert result.raw_edge_score > 0
