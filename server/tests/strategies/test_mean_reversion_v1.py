from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.market.candles import Candle
from app.models.enums import (
    DerivativesFlow,
    Direction,
    LiquidityRegime,
    TrendRegime,
    VolatilityRegime,
)
from app.regime.classifier import RegimeResult
from app.strategies.mean_reversion_v1 import evaluate_mean_reversion_v1
from app.strategies.result_v2 import DataQualityState, StrategyResultV2


EVALUATED_AT = datetime(2026, 8, 20, 17, 0, tzinfo=UTC)


def _regime(
    *,
    trend: TrendRegime = TrendRegime.RANGE,
    volatility: VolatilityRegime = VolatilityRegime.NORMAL,
    liquidity: LiquidityRegime = LiquidityRegime.GOOD,
) -> RegimeResult:
    return RegimeResult(
        trend=trend,
        trend_score=0,
        volatility=volatility,
        liquidity=liquidity,
        derivatives_flow=DerivativesFlow.NEUTRAL,
    )


def _bar(index: int, open_: str, high: str, low: str, close: str, *, closed: bool = True) -> Candle:
    return Candle(
        open_time=EVALUATED_AT - timedelta(hours=39 - index),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume_units=Decimal("1000"),
        is_closed=closed,
        source="fixture",
    )


def _range_bars(*, side: Direction) -> list[Candle]:
    bars: list[Candle] = []
    # Stable range around 100 gives a robust center and realized-vol baseline.
    closes = [
        Decimal("99"), Decimal("100"), Decimal("101"), Decimal("100"),
        Decimal("99.5"), Decimal("100.5"), Decimal("101"), Decimal("99"),
    ]
    for index in range(37):
        close = closes[index % len(closes)]
        bars.append(
            _bar(
                index,
                str(close),
                str(close + Decimal("1")),
                str(close - Decimal("1")),
                str(close),
            )
        )

    if side is Direction.LONG:
        # Exhaustion below the range followed by a closed-bar reversion toward center.
        bars.append(_bar(37, "99", "99.5", "94", "95"))
        bars.append(_bar(38, "95", "98", "94.5", "97.5"))
        bars.append(_bar(39, "97.5", "99", "97", "98.5"))
    else:
        bars.append(_bar(37, "101", "106", "100.5", "105"))
        bars.append(_bar(38, "105", "105.5", "102", "102.5"))
        bars.append(_bar(39, "102.5", "103", "101", "101.5"))
    return bars


@pytest.mark.parametrize("side", [Direction.LONG, Direction.SHORT])
def test_mean_reversion_v1_emits_only_confirmed_range_reversion(side: Direction) -> None:
    result = evaluate_mean_reversion_v1(
        instrument_id="CRYPTO:BTCUSDT",
        bars=_range_bars(side=side),
        regime=_regime(),
        evaluated_at=EVALUATED_AT,
    )

    assert isinstance(result, StrategyResultV2)
    assert result.strategy_family == "MEAN_REVERSION"
    assert result.strategy_version == "mean_reversion_v1"
    assert result.direction is side
    assert result.raw_edge_score > 0
    assert result.entry_hypothesis.kind == "REVERSION_CONFIRMATION"
    assert result.data_quality_state is DataQualityState.GOOD
    assert "hard invalidation" in result.invalidation.lower()
    assert {feature.name for feature in result.feature_provenance} >= {
        "robust_center",
        "deviation_atr",
        "realized_vol_atr",
        "reversion_progress",
        "entry_reference",
    }
    assert all(feature.tradable_at <= result.evaluated_at for feature in result.feature_provenance)


@pytest.mark.parametrize(
    "regime",
    [
        _regime(trend=TrendRegime.UPTREND),
        _regime(trend=TrendRegime.DOWNTREND),
        _regime(trend=TrendRegime.TRANSITION),
        _regime(volatility=VolatilityRegime.HIGH),
        _regime(volatility=VolatilityRegime.EXTREME),
        _regime(liquidity=LiquidityRegime.THIN),
        _regime(liquidity=LiquidityRegime.UNTRADEABLE),
    ],
)
def test_mean_reversion_v1_fails_closed_outside_range_normal_liquid_regime(
    regime: RegimeResult,
) -> None:
    assert (
        evaluate_mean_reversion_v1(
            instrument_id="CRYPTO:BTCUSDT",
            bars=_range_bars(side=Direction.LONG),
            regime=regime,
            evaluated_at=EVALUATED_AT,
        )
        is None
    )


def test_mean_reversion_v1_requires_exhaustion_and_closed_bar_reversion_evidence() -> None:
    bars = _range_bars(side=Direction.LONG)
    bars[-2] = _bar(38, "95", "96", "94.5", "95.2")
    bars[-1] = _bar(39, "95.2", "95.5", "94.8", "95.0")

    assert (
        evaluate_mean_reversion_v1(
            instrument_id="MOEX:FUT:SIU6",
            bars=bars,
            regime=_regime(),
            evaluated_at=EVALUATED_AT,
        )
        is None
    )


def test_mean_reversion_v1_ignores_forming_and_future_bars() -> None:
    bars = _range_bars(side=Direction.LONG)
    baseline = evaluate_mean_reversion_v1(
        instrument_id="CRYPTO:BTCUSDT",
        bars=bars,
        regime=_regime(),
        evaluated_at=EVALUATED_AT,
    )

    forming = Candle(
        open_time=EVALUATED_AT,
        open=Decimal("98.5"),
        high=Decimal("150"),
        low=Decimal("98"),
        close=Decimal("149"),
        is_closed=False,
        source="forming-fixture",
    )
    future = Candle(
        open_time=EVALUATED_AT + timedelta(hours=1),
        open=Decimal("98.5"),
        high=Decimal("200"),
        low=Decimal("98"),
        close=Decimal("199"),
        is_closed=True,
        source="future-fixture",
    )

    with_unavailable = evaluate_mean_reversion_v1(
        instrument_id="CRYPTO:BTCUSDT",
        bars=[*bars, forming, future],
        regime=_regime(),
        evaluated_at=EVALUATED_AT,
    )

    assert with_unavailable == baseline


def test_mean_reversion_v1_needs_enough_history_for_robust_center_and_volatility() -> None:
    assert (
        evaluate_mean_reversion_v1(
            instrument_id="CRYPTO:BTCUSDT",
            bars=_range_bars(side=Direction.LONG)[-12:],
            regime=_regime(),
            evaluated_at=EVALUATED_AT,
        )
        is None
    )
