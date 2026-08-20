from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.enums import Direction
from app.regime.classifier_v2 import RegimeClassificationV2, RegimeFeatureProvenance
from app.regime.strategy_gate_v1 import RegimeGateDecision, gate_strategy_regime
from app.strategies.result_v2 import (
    DataQualityState,
    EntryHypothesis,
    ExplanationComponent,
    FeatureProvenance,
    StrategyHorizon,
    StrategyResultV2,
)


AT = datetime(2026, 8, 20, 19, 0, tzinfo=UTC)


def _strategy(
    *,
    family: str = "MOMENTUM",
    version: str = "momentum_v2",
    quality: DataQualityState = DataQualityState.GOOD,
    declared_compatibility: tuple[str, ...] = ("TREND", "NORMAL_VOL", "HIGH_VOL"),
    evaluated_at: datetime = AT,
) -> StrategyResultV2:
    return StrategyResultV2(
        strategy_family=family,
        strategy_version=version,
        direction=Direction.LONG,
        raw_edge_score=Decimal("0.75"),
        entry_hypothesis=EntryHypothesis(
            kind="TEST",
            reference=Decimal("100"),
            rationale="test entry hypothesis",
        ),
        invalidation="test invalidation",
        horizon=StrategyHorizon(value=24, unit="HOURS"),
        feature_provenance=(
            FeatureProvenance(
                name="candidate_feature",
                value="0.75",
                source="candidate-fixture",
                observed_at=evaluated_at,
                tradable_at=evaluated_at,
            ),
        ),
        regime_compatibility=declared_compatibility,
        data_quality_state=quality,
        explanation_components=(
            ExplanationComponent(
                name="candidate_edge",
                contribution=Decimal("0.75"),
                detail="candidate edge fixture",
            ),
        ),
        evaluated_at=evaluated_at,
    )


def _regime(
    *,
    trend: str = "0.90",
    low_vol: str = "0.05",
    normal_vol: str = "0.45",
    high_vol: str = "0.50",
    liquidity_stress: str = "0.05",
    stress: str = "0.10",
    version: str = "regime_classifier_v2",
    evaluated_at: datetime = AT,
) -> RegimeClassificationV2:
    trend_probability = Decimal(trend)
    low = Decimal(low_vol)
    normal = Decimal(normal_vol)
    high = Decimal(high_vol)
    return RegimeClassificationV2(
        version=version,
        trend_probability=trend_probability,
        range_probability=Decimal("1") - trend_probability,
        low_vol_probability=low,
        normal_vol_probability=normal,
        high_vol_probability=high,
        liquidity_stress_score=Decimal(liquidity_stress),
        stress_probability=Decimal(stress),
        feature_provenance=(
            RegimeFeatureProvenance(
                name="fixture",
                value=trend_probability,
                source="regime-fixture",
                observed_at=evaluated_at,
                tradable_at=evaluated_at,
            ),
        ),
        evaluated_at=evaluated_at,
    )


def test_gate_is_versioned_admission_evidence_not_risk_or_execution() -> None:
    result = gate_strategy_regime(_strategy(), _regime())

    assert result.policy_version == "strategy_regime_gate_v1"
    assert result.classifier_version == "regime_classifier_v2"
    assert result.strategy_family == "MOMENTUM"
    assert result.strategy_version == "momentum_v2"
    assert result.decision is RegimeGateDecision.ALLOW
    assert Decimal("0") <= result.compatibility_score <= Decimal("1")
    for forbidden in (
        "risk_pct",
        "risk_amount",
        "quantity",
        "leverage",
        "order_intent",
        "stop",
        "targets",
    ):
        assert not hasattr(result, forbidden)


def test_momentum_allows_strong_trend_and_blocks_strong_range() -> None:
    strong_trend = gate_strategy_regime(_strategy(), _regime(trend="0.90"))
    strong_range = gate_strategy_regime(_strategy(), _regime(trend="0.10"))

    assert strong_trend.decision is RegimeGateDecision.ALLOW
    assert strong_range.decision is RegimeGateDecision.BLOCK
    assert strong_trend.compatibility_score > strong_range.compatibility_score


def test_mean_reversion_allows_clean_range_and_reduces_mixed_structure() -> None:
    candidate = _strategy(
        family="MEAN_REVERSION",
        version="mean_reversion_v1",
        declared_compatibility=("RANGE", "LOW_VOL", "NORMAL_VOL"),
    )
    clean_range = gate_strategy_regime(
        candidate,
        _regime(
            trend="0.10",
            low_vol="0.45",
            normal_vol="0.45",
            high_vol="0.10",
        ),
    )
    mixed = gate_strategy_regime(
        candidate,
        _regime(
            trend="0.55",
            low_vol="0.30",
            normal_vol="0.50",
            high_vol="0.20",
        ),
    )

    assert clean_range.decision is RegimeGateDecision.ALLOW
    assert mixed.decision is RegimeGateDecision.REDUCE


def test_breakout_is_blocked_by_independent_market_stress() -> None:
    candidate = _strategy(
        family="BREAKOUT",
        version="breakout_v2",
        declared_compatibility=("TREND", "NORMAL_VOL", "HIGH_VOL"),
    )
    stressed = gate_strategy_regime(
        candidate,
        _regime(
            trend="0.95",
            low_vol="0.05",
            normal_vol="0.45",
            high_vol="0.50",
            liquidity_stress="0.75",
            stress="0.85",
        ),
    )

    assert stressed.decision is RegimeGateDecision.BLOCK
    assert stressed.stress_fit <= Decimal("0.25")
    assert "STRESS_INCOMPATIBLE" in stressed.reasons


def test_crypto_carry_is_not_forced_into_trend_range_axis() -> None:
    carry = _strategy(
        family="CRYPTO_CARRY",
        version="crypto_carry_v1",
        declared_compatibility=("CARRY", "FUNDING_PERSISTENT"),
    )

    trend = gate_strategy_regime(carry, _regime(trend="0.95"))
    range_ = gate_strategy_regime(carry, _regime(trend="0.05"))
    stressed = gate_strategy_regime(
        carry,
        _regime(trend="0.50", liquidity_stress="0.80", stress="0.90"),
    )

    assert trend.decision is RegimeGateDecision.ALLOW
    assert range_.decision is RegimeGateDecision.ALLOW
    assert trend.structure_fit is None
    assert range_.structure_fit is None
    assert stressed.decision is RegimeGateDecision.BLOCK


def test_self_declared_regime_compatibility_cannot_widen_authoritative_policy() -> None:
    dishonest_momentum = _strategy(declared_compatibility=("RANGE", "LOW_VOL"))
    result = gate_strategy_regime(
        dishonest_momentum,
        _regime(
            trend="0.05",
            low_vol="0.90",
            normal_vol="0.05",
            high_vol="0.05",
        ),
    )

    assert result.decision is RegimeGateDecision.BLOCK
    assert "STRUCTURE_INCOMPATIBLE" in result.reasons


def test_unknown_strategy_version_fails_closed() -> None:
    result = gate_strategy_regime(
        _strategy(version="momentum_v999"),
        _regime(),
    )

    assert result.decision is RegimeGateDecision.BLOCK
    assert result.compatibility_score == Decimal("0")
    assert result.reasons == ("NO_POLICY_FOR_STRATEGY_VERSION",)


def test_unsupported_classifier_version_fails_closed() -> None:
    result = gate_strategy_regime(
        _strategy(),
        _regime(version="regime_classifier_v999"),
    )

    assert result.decision is RegimeGateDecision.BLOCK
    assert result.reasons == ("UNSUPPORTED_CLASSIFIER_VERSION",)


def test_point_in_time_mismatch_fails_closed() -> None:
    later = AT + timedelta(minutes=1)
    earlier = AT - timedelta(minutes=1)

    future_regime = gate_strategy_regime(_strategy(), _regime(evaluated_at=later))
    stale_regime = gate_strategy_regime(_strategy(), _regime(evaluated_at=earlier))

    assert future_regime.decision is RegimeGateDecision.BLOCK
    assert stale_regime.decision is RegimeGateDecision.BLOCK
    assert future_regime.reasons == ("POINT_IN_TIME_MISMATCH",)
    assert stale_regime.reasons == ("POINT_IN_TIME_MISMATCH",)


def test_blocked_data_quality_cannot_be_allowed_by_favorable_regime() -> None:
    blocked = gate_strategy_regime(
        _strategy(quality=DataQualityState.BLOCKED),
        _regime(),
    )
    degraded = gate_strategy_regime(
        _strategy(quality=DataQualityState.DEGRADED),
        _regime(),
    )

    assert blocked.decision is RegimeGateDecision.BLOCK
    assert blocked.reasons == ("DATA_QUALITY_BLOCKED",)
    assert degraded.decision is RegimeGateDecision.REDUCE
    assert "DATA_QUALITY_DEGRADED" in degraded.reasons


def test_gate_is_deterministic_for_identical_inputs() -> None:
    strategy = _strategy()
    regime = _regime()

    assert gate_strategy_regime(strategy, regime) == gate_strategy_regime(strategy, regime)
