from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.enums import Direction
from app.strategies.result_v2 import (
    DataQualityState,
    EntryHypothesis,
    ExplanationComponent,
    FeatureProvenance,
    StrategyHorizon,
    StrategyResultV2,
)


def _result(**overrides) -> StrategyResultV2:
    evaluated_at = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)
    values = dict(
        strategy_family="momentum",
        strategy_version="momentum_v2",
        direction=Direction.LONG,
        raw_edge_score=Decimal("0.1842"),
        entry_hypothesis=EntryHypothesis(
            kind="BREAKOUT_CONFIRMATION",
            reference=Decimal("64231.50"),
            lower=Decimal("64190.00"),
            upper=Decimal("64250.00"),
            rationale="close above volatility-adjusted trigger",
        ),
        invalidation="trend strength falls below candidate threshold",
        horizon=StrategyHorizon(value=24, unit="HOURS"),
        feature_provenance=(
            FeatureProvenance(
                name="normalized_momentum_24h",
                value="0.1842",
                source="closed_bars",
                observed_at=evaluated_at - timedelta(minutes=1),
                tradable_at=evaluated_at,
            ),
        ),
        regime_compatibility=("UPTREND", "NORMAL_VOL"),
        data_quality_state=DataQualityState.GOOD,
        explanation_components=(
            ExplanationComponent(
                name="trend_strength",
                contribution=Decimal("0.12"),
                detail="multi-horizon trend agrees",
            ),
        ),
        evaluated_at=evaluated_at,
    )
    values.update(overrides)
    return StrategyResultV2(**values)


def test_strategy_result_v2_is_immutable_signal_evidence_not_trade_plan() -> None:
    result = _result()

    assert result.direction is Direction.LONG
    assert result.raw_edge_score == Decimal("0.1842")
    assert result.entry_hypothesis.reference == Decimal("64231.50")
    assert result.horizon.value == 24
    assert result.feature_provenance[0].tradable_at == result.evaluated_at

    names = {item.name for item in fields(StrategyResultV2)}
    assert "risk_pct" not in names
    assert "risk_amount" not in names
    assert "quantity" not in names
    assert "leverage" not in names
    assert "order_intent" not in names
    assert "stop" not in names
    assert "targets" not in names
    assert "execution_mode" not in names

    with pytest.raises(FrozenInstanceError):
        result.raw_edge_score = Decimal("9")  # type: ignore[misc]


def test_strategy_result_v2_rejects_feature_not_tradable_at_evaluation_time() -> None:
    evaluated_at = datetime(2026, 8, 20, 16, 0, tzinfo=UTC)
    future_feature = FeatureProvenance(
        name="future_close",
        value="64300",
        source="closed_bars",
        observed_at=evaluated_at,
        tradable_at=evaluated_at + timedelta(microseconds=1),
    )

    with pytest.raises(ValueError, match="tradable_at"):
        _result(evaluated_at=evaluated_at, feature_provenance=(future_feature,))


def test_strategy_result_v2_fails_closed_on_invalid_identity_horizon_and_entry_zone() -> None:
    with pytest.raises(ValueError, match="strategy_family"):
        _result(strategy_family="")

    with pytest.raises(ValueError, match="strategy_version"):
        _result(strategy_version=" ")

    with pytest.raises(ValueError, match="horizon"):
        _result(horizon=StrategyHorizon(value=0, unit="HOURS"))

    with pytest.raises(ValueError, match="entry hypothesis"):
        _result(
            entry_hypothesis=EntryHypothesis(
                kind="ZONE",
                reference=Decimal("100"),
                lower=Decimal("101"),
                upper=Decimal("102"),
                rationale="invalid reference outside zone",
            )
        )


def test_strategy_result_v2_keeps_data_quality_explicit_instead_of_hiding_candidate() -> None:
    blocked = _result(data_quality_state=DataQualityState.BLOCKED)

    assert blocked.data_quality_state is DataQualityState.BLOCKED
    assert blocked.raw_edge_score == Decimal("0.1842")
