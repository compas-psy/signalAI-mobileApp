from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.market.candles import Candle
from app.models.enums import Direction, QualityFlag
from app.strategies.momentum_v2 import evaluate_momentum_v2
from app.strategies.result_v2 import DataQualityState, StrategyResultV2


EVALUATED_AT = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)


def _trend(
    *,
    count: int = 48,
    start: Decimal = Decimal("100"),
    step: Decimal = Decimal("2"),
    hours: int = 1,
    end_at: datetime = EVALUATED_AT,
) -> list[Candle]:
    first = end_at - timedelta(hours=hours * (count - 1))
    bars: list[Candle] = []
    price = start
    for index in range(count):
        close = price + step
        bars.append(
            Candle(
                open_time=first + timedelta(hours=hours * index),
                open=price,
                high=max(price, close) + Decimal("1"),
                low=min(price, close) - Decimal("1"),
                close=close,
                volume_units=Decimal("1000"),
                is_closed=True,
                source="fixture",
            )
        )
        price = close
    return bars


def _chop(*, count: int = 48) -> list[Candle]:
    first = EVALUATED_AT - timedelta(hours=count - 1)
    price = Decimal("100")
    bars: list[Candle] = []
    for index in range(count):
        step = Decimal("3") if index % 2 == 0 else Decimal("-3")
        close = price + step
        bars.append(
            Candle(
                open_time=first + timedelta(hours=index),
                open=price,
                high=max(price, close) + Decimal("1"),
                low=min(price, close) - Decimal("1"),
                close=close,
                volume_units=Decimal("1000"),
                is_closed=True,
                source="fixture",
            )
        )
        price = close
    return bars


@pytest.mark.parametrize("instrument_id", ["CRYPTO:BTCUSDT", "MOEX:FUT:SIU6"])
def test_momentum_v2_emits_long_candidate_for_confirmed_multi_horizon_trend(
    instrument_id: str,
) -> None:
    result = evaluate_momentum_v2(
        instrument_id=instrument_id,
        context_bars=_trend(hours=4, step=Decimal("5")),
        setup_bars=_trend(hours=1, step=Decimal("3")),
        trigger_bars=_trend(hours=1, step=Decimal("2")),
        evaluated_at=EVALUATED_AT,
    )

    assert isinstance(result, StrategyResultV2)
    assert result.strategy_family == "MOMENTUM"
    assert result.strategy_version == "momentum_v2"
    assert result.direction is Direction.LONG
    assert result.raw_edge_score > 0
    assert result.entry_hypothesis.kind == "BREAKOUT_CONFIRMATION"
    assert result.entry_hypothesis.reference == Decimal(result.feature_provenance[-1].value)
    assert result.data_quality_state is DataQualityState.GOOD
    assert {feature.name for feature in result.feature_provenance} >= {
        "context_momentum_atr",
        "setup_momentum_atr",
        "trigger_breakout_atr",
        "trigger_efficiency",
        "entry_reference",
    }
    assert all(feature.tradable_at <= result.evaluated_at for feature in result.feature_provenance)


def test_momentum_v2_emits_short_candidate_symmetrically() -> None:
    result = evaluate_momentum_v2(
        instrument_id="CRYPTO:ETHUSDT",
        context_bars=_trend(hours=4, start=Decimal("1000"), step=Decimal("-5")),
        setup_bars=_trend(hours=1, start=Decimal("500"), step=Decimal("-3")),
        trigger_bars=_trend(hours=1, start=Decimal("300"), step=Decimal("-2")),
        evaluated_at=EVALUATED_AT,
    )

    assert isinstance(result, StrategyResultV2)
    assert result.direction is Direction.SHORT
    assert result.raw_edge_score > 0
    assert "below" in result.entry_hypothesis.rationale


def test_momentum_v2_anti_chop_filter_returns_no_candidate() -> None:
    result = evaluate_momentum_v2(
        instrument_id="CRYPTO:BTCUSDT",
        context_bars=_trend(hours=4, step=Decimal("5")),
        setup_bars=_trend(hours=1, step=Decimal("3")),
        trigger_bars=_chop(),
        evaluated_at=EVALUATED_AT,
    )

    assert result is None


def test_momentum_v2_ignores_forming_and_future_bars_no_lookahead() -> None:
    context = _trend(hours=4, step=Decimal("5"))
    setup = _trend(hours=1, step=Decimal("3"))
    trigger = _trend(hours=1, step=Decimal("2"))
    baseline = evaluate_momentum_v2(
        instrument_id="CRYPTO:BTCUSDT",
        context_bars=context,
        setup_bars=setup,
        trigger_bars=trigger,
        evaluated_at=EVALUATED_AT,
    )

    future = Candle(
        open_time=EVALUATED_AT + timedelta(hours=1),
        open=Decimal("1000"),
        high=Decimal("5001"),
        low=Decimal("999"),
        close=Decimal("5000"),
        is_closed=True,
        source="future-fixture",
    )
    forming = Candle(
        open_time=EVALUATED_AT,
        open=trigger[-1].close,
        high=Decimal("9999"),
        low=trigger[-1].close - Decimal("1"),
        close=Decimal("9998"),
        is_closed=False,
        source="forming-fixture",
    )
    with_future = evaluate_momentum_v2(
        instrument_id="CRYPTO:BTCUSDT",
        context_bars=[*context, future],
        setup_bars=setup,
        trigger_bars=[*trigger, forming, future],
        evaluated_at=EVALUATED_AT,
    )

    assert with_future == baseline


def test_momentum_v2_preserves_blocked_data_quality_as_evidence() -> None:
    trigger = _trend(hours=1, step=Decimal("2"))
    trigger[-1] = replace(trigger[-1], quality_flags=(QualityFlag.STALE.value,))

    result = evaluate_momentum_v2(
        instrument_id="MOEX:FUT:RIU6",
        context_bars=_trend(hours=4, step=Decimal("5")),
        setup_bars=_trend(hours=1, step=Decimal("3")),
        trigger_bars=trigger,
        evaluated_at=EVALUATED_AT,
    )

    assert isinstance(result, StrategyResultV2)
    assert result.data_quality_state is DataQualityState.BLOCKED
    assert result.raw_edge_score > 0


def test_momentum_v2_needs_enough_history_for_atr_and_breakout() -> None:
    result = evaluate_momentum_v2(
        instrument_id="CRYPTO:BTCUSDT",
        context_bars=_trend(count=10, hours=4, step=Decimal("5")),
        setup_bars=_trend(count=10, hours=1, step=Decimal("3")),
        trigger_bars=_trend(count=10, hours=1, step=Decimal("2")),
        evaluated_at=EVALUATED_AT,
    )

    assert result is None
