from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.regime.classifier_v2 import RegimeFeatureVector, classify_regime_v2


EVALUATED_AT = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)


def _features(
    *,
    trend_strength: str = "0.50",
    realized_vol_score: str = "0.50",
    vol_of_vol_score: str = "0.30",
    chop_score: str = "0.50",
    liquidity_spread_stress: str = "0.20",
    stress_score: str = "0.10",
    tradable_at: datetime = EVALUATED_AT,
) -> RegimeFeatureVector:
    return RegimeFeatureVector(
        trend_strength=Decimal(trend_strength),
        realized_vol_score=Decimal(realized_vol_score),
        vol_of_vol_score=Decimal(vol_of_vol_score),
        chop_score=Decimal(chop_score),
        liquidity_spread_stress=Decimal(liquidity_spread_stress),
        stress_score=Decimal(stress_score),
        observed_at=EVALUATED_AT,
        tradable_at=tradable_at,
        source="fixture-deterministic-features",
    )


def test_classifier_returns_probabilistic_axes_not_one_magic_label() -> None:
    result = classify_regime_v2(_features(), evaluated_at=EVALUATED_AT)

    assert result is not None
    assert result.version == "regime_classifier_v2"
    assert result.trend_probability + result.range_probability == Decimal("1")
    assert (
        result.low_vol_probability
        + result.normal_vol_probability
        + result.high_vol_probability
        == Decimal("1")
    )
    assert Decimal("0") <= result.liquidity_stress_score <= Decimal("1")
    assert Decimal("0") <= result.stress_probability <= Decimal("1")
    assert not hasattr(result, "label")
    assert not hasattr(result, "regime")


def test_strong_clean_trend_lifts_trend_probability() -> None:
    result = classify_regime_v2(
        _features(
            trend_strength="0.95",
            chop_score="0.05",
            realized_vol_score="0.45",
            vol_of_vol_score="0.15",
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result is not None
    assert result.trend_probability >= Decimal("0.85")
    assert result.trend_probability > result.range_probability


def test_high_chop_weak_trend_lifts_range_probability() -> None:
    result = classify_regime_v2(
        _features(trend_strength="0.10", chop_score="0.95"),
        evaluated_at=EVALUATED_AT,
    )

    assert result is not None
    assert result.range_probability >= Decimal("0.85")
    assert result.range_probability > result.trend_probability


def test_realized_vol_and_vol_of_vol_lift_high_vol_probability() -> None:
    calm = classify_regime_v2(
        _features(realized_vol_score="0.35", vol_of_vol_score="0.10"),
        evaluated_at=EVALUATED_AT,
    )
    hot = classify_regime_v2(
        _features(realized_vol_score="0.95", vol_of_vol_score="0.90"),
        evaluated_at=EVALUATED_AT,
    )

    assert calm is not None and hot is not None
    assert hot.high_vol_probability > calm.high_vol_probability
    assert hot.high_vol_probability >= Decimal("0.70")


def test_low_realized_vol_lifts_low_vol_probability() -> None:
    result = classify_regime_v2(
        _features(realized_vol_score="0.05", vol_of_vol_score="0.05"),
        evaluated_at=EVALUATED_AT,
    )

    assert result is not None
    assert result.low_vol_probability > result.normal_vol_probability
    assert result.low_vol_probability > result.high_vol_probability


def test_liquidity_spread_and_stress_features_raise_stress_probability() -> None:
    healthy = classify_regime_v2(
        _features(liquidity_spread_stress="0.05", stress_score="0.05", vol_of_vol_score="0.10"),
        evaluated_at=EVALUATED_AT,
    )
    stressed = classify_regime_v2(
        _features(liquidity_spread_stress="0.95", stress_score="0.90", vol_of_vol_score="0.80"),
        evaluated_at=EVALUATED_AT,
    )

    assert healthy is not None and stressed is not None
    assert stressed.liquidity_stress_score == Decimal("0.95")
    assert stressed.stress_probability > healthy.stress_probability
    assert stressed.stress_probability >= Decimal("0.80")


def test_feature_provenance_covers_all_backlog_deterministic_inputs() -> None:
    result = classify_regime_v2(_features(), evaluated_at=EVALUATED_AT)

    assert result is not None
    assert {feature.name for feature in result.feature_provenance} == {
        "trend_strength",
        "realized_vol_score",
        "vol_of_vol_score",
        "chop_score",
        "liquidity_spread_stress",
        "stress_score",
    }
    assert all(feature.tradable_at <= EVALUATED_AT for feature in result.feature_provenance)


def test_future_not_yet_tradable_features_fail_closed_no_lookahead() -> None:
    future = replace(
        _features(),
        tradable_at=EVALUATED_AT + timedelta(seconds=1),
    )

    assert classify_regime_v2(future, evaluated_at=EVALUATED_AT) is None


def test_classifier_is_deterministic_for_same_feature_vector() -> None:
    features = _features(
        trend_strength="0.72",
        realized_vol_score="0.61",
        vol_of_vol_score="0.44",
        chop_score="0.23",
        liquidity_spread_stress="0.17",
        stress_score="0.28",
    )

    first = classify_regime_v2(features, evaluated_at=EVALUATED_AT)
    second = classify_regime_v2(features, evaluated_at=EVALUATED_AT)

    assert first == second


def test_classifier_does_not_contain_strategy_gating_decisions() -> None:
    result = classify_regime_v2(_features(), evaluated_at=EVALUATED_AT)

    assert result is not None
    assert not hasattr(result, "allowed")
    assert not hasattr(result, "blocked")
    assert not hasattr(result, "strategy_family")


def test_feature_scores_must_be_normalized() -> None:
    with pytest.raises(ValueError, match="trend_strength"):
        _features(trend_strength="1.01")
    with pytest.raises(ValueError, match="liquidity_spread_stress"):
        _features(liquidity_spread_stress="-0.01")
