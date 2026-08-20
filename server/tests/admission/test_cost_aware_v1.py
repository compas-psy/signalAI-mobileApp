from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.admission.cost_aware_v1 import (
    CostAwareCandidateInput,
    evaluate_cost_aware_admission,
    rank_cost_aware_candidates,
)
from app.backtest.costs import CostModel, ResolvedCostModel
from app.models.enums import Direction
from app.regime.strategy_gate_v1 import (
    RegimeGateDecision,
    StrategyRegimeGateResult,
)
from app.strategies.result_v2 import (
    DataQualityState,
    EntryHypothesis,
    ExplanationComponent,
    FeatureProvenance,
    StrategyHorizon,
    StrategyResultV2,
)


AT = datetime(2026, 8, 20, 20, 0, tzinfo=UTC)


def _strategy(
    *,
    family: str = "MOMENTUM",
    version: str = "momentum_v2",
    raw_edge_score: str = "0.75",
    provenance: tuple[FeatureProvenance, ...] | None = None,
) -> StrategyResultV2:
    return StrategyResultV2(
        strategy_family=family,
        strategy_version=version,
        direction=Direction.LONG,
        raw_edge_score=Decimal(raw_edge_score),
        entry_hypothesis=EntryHypothesis(
            kind="TEST",
            reference=Decimal("100"),
            rationale="candidate fixture",
        ),
        invalidation="fixture invalidation",
        horizon=StrategyHorizon(value=24, unit="HOURS"),
        feature_provenance=provenance
        or (
            FeatureProvenance(
                name="candidate_feature",
                value=raw_edge_score,
                source="candidate-fixture",
                observed_at=AT,
                tradable_at=AT,
            ),
        ),
        regime_compatibility=("TREND", "NORMAL_VOL"),
        data_quality_state=DataQualityState.GOOD,
        explanation_components=(
            ExplanationComponent(
                name="candidate_edge",
                contribution=Decimal(raw_edge_score),
                detail="candidate fixture",
            ),
        ),
        evaluated_at=AT,
    )


def _gate(
    strategy: StrategyResultV2,
    *,
    decision: RegimeGateDecision = RegimeGateDecision.ALLOW,
    compatibility: str = "0.80",
) -> StrategyRegimeGateResult:
    score = Decimal(compatibility)
    return StrategyRegimeGateResult(
        policy_version="strategy_regime_gate_v1",
        classifier_version="regime_classifier_v2",
        strategy_family=strategy.strategy_family,
        strategy_version=strategy.strategy_version,
        decision=decision,
        compatibility_score=score,
        structure_fit=score,
        volatility_fit=score,
        stress_fit=score,
        reasons=("fixture",),
    )


def _resolved_costs(
    *,
    model: CostModel | None = None,
    effective_at: datetime = AT,
) -> ResolvedCostModel:
    return ResolvedCostModel(
        venue="BYBIT",
        model=model
        or CostModel(
            maker_fee_bps=Decimal("1"),
            taker_fee_bps=Decimal("2"),
            entry_slippage_bps=Decimal("3"),
            exit_slippage_bps=Decimal("4"),
            funding_bps_per_interval=Decimal("5"),
            spread_bps=Decimal("2"),
        ),
        source_ref="fixture:bybit-costs",
        effective_at=effective_at,
    )


def _candidate(
    *,
    key: str = "BTCUSDT:LONG",
    strategy: StrategyResultV2 | None = None,
    gate: StrategyRegimeGateResult | None = None,
    costs: ResolvedCostModel | None = None,
    gross: str = "50",
    carry_cost: str = "4",
    liquidity_penalty: str = "3",
    uncertainty: str = "10",
    funding_intervals: int = 1,
    entry_maker: bool = False,
    exit_maker: bool = False,
) -> CostAwareCandidateInput:
    selected_strategy = strategy or _strategy()
    return CostAwareCandidateInput(
        candidate_key=key,
        strategy=selected_strategy,
        regime_gate=gate or _gate(selected_strategy),
        resolved_cost_model=costs or _resolved_costs(),
        expected_gross_edge_bps=Decimal(gross),
        expected_carry_cost_bps=Decimal(carry_cost),
        liquidity_penalty_bps=Decimal(liquidity_penalty),
        uncertainty_bps=Decimal(uncertainty),
        funding_intervals=funding_intervals,
        entry_maker=entry_maker,
        exit_maker=exit_maker,
    )


def test_formula_is_auditable_in_bps_and_reuses_canonical_cost_model() -> None:
    result = evaluate_cost_aware_admission(_candidate())

    # taker fees 2+2, slippage 3+4, spread 2, funding 5 = 18 bps
    assert result.expected_fee_bps == Decimal("4")
    assert result.expected_slippage_bps == Decimal("7")
    assert result.expected_spread_bps == Decimal("2")
    assert result.expected_funding_cost_bps == Decimal("5")
    assert result.expected_execution_cost_bps == Decimal("18")
    assert result.expected_carry_cost_bps == Decimal("4")
    assert result.liquidity_penalty_bps == Decimal("3")
    assert result.expected_total_cost_bps == Decimal("25")
    assert result.expected_net_edge_bps == Decimal("25")
    assert result.uncertainty_bps == Decimal("10")
    assert result.edge_surplus_bps == Decimal("15")
    assert result.decision is RegimeGateDecision.ALLOW
    assert result.cost_source_ref == "fixture:bybit-costs"
    assert result.cost_policy_version == "cost_aware_admission_v1"


def test_net_edge_must_exceed_uncertainty_or_candidate_is_no_signal() -> None:
    result = evaluate_cost_aware_admission(
        _candidate(gross="35", carry_cost="4", liquidity_penalty="3", uncertainty="10")
    )

    assert result.expected_net_edge_bps == Decimal("10")
    assert result.edge_surplus_bps == Decimal("0")
    assert result.decision is RegimeGateDecision.BLOCK
    assert "NET_EDGE_NOT_ABOVE_UNCERTAINTY" in result.reasons


def test_regime_block_is_monotonic_even_with_large_net_edge() -> None:
    strategy = _strategy()
    result = evaluate_cost_aware_admission(
        _candidate(
            strategy=strategy,
            gate=_gate(strategy, decision=RegimeGateDecision.BLOCK),
            gross="500",
        )
    )

    assert result.decision is RegimeGateDecision.BLOCK
    assert "REGIME_BLOCKED" in result.reasons


def test_regime_reduce_caps_cost_eligible_candidate_at_reduce() -> None:
    strategy = _strategy()
    result = evaluate_cost_aware_admission(
        _candidate(
            strategy=strategy,
            gate=_gate(strategy, decision=RegimeGateDecision.REDUCE),
            gross="100",
        )
    )

    assert result.edge_surplus_bps > 0
    assert result.decision is RegimeGateDecision.REDUCE
    assert "REGIME_REDUCED" in result.reasons


def test_cost_snapshot_must_match_candidate_point_in_time() -> None:
    mismatched = _resolved_costs(
        effective_at=datetime(2026, 8, 20, 19, 59, tzinfo=UTC)
    )
    result = evaluate_cost_aware_admission(_candidate(costs=mismatched))

    assert result.decision is RegimeGateDecision.BLOCK
    assert "COST_POINT_IN_TIME_MISMATCH" in result.reasons


def test_strategy_and_gate_identity_mismatch_fails_closed() -> None:
    strategy = _strategy()
    other = _strategy(family="BREAKOUT", version="breakout_v2")
    result = evaluate_cost_aware_admission(
        _candidate(strategy=strategy, gate=_gate(other))
    )

    assert result.decision is RegimeGateDecision.BLOCK
    assert result.reasons == ("STRATEGY_GATE_IDENTITY_MISMATCH",)


def test_carry_provenance_does_not_cause_execution_cost_double_count() -> None:
    strategy = _strategy(
        family="CRYPTO_CARRY",
        version="crypto_carry_v1",
        provenance=(
            FeatureProvenance(
                name="execution_cost_bps",
                value="18",
                source="canonical_cost_model",
                observed_at=AT,
                tradable_at=AT,
            ),
            FeatureProvenance(
                name="net_carry_bps",
                value="20",
                source="carry_net_after_costs",
                observed_at=AT,
                tradable_at=AT,
            ),
        ),
    )
    result = evaluate_cost_aware_admission(
        _candidate(
            strategy=strategy,
            gate=_gate(strategy),
            gross="60",
            carry_cost="12",
            liquidity_penalty="0",
            uncertainty="5",
            funding_intervals=0,
        )
    )

    # SAI-060 uses the explicit gross decomposition and the resolved CostModel
    # exactly once; it never re-subtracts StrategyResult provenance fields.
    assert result.expected_execution_cost_bps == Decimal("13")
    assert result.expected_total_cost_bps == Decimal("25")
    assert result.expected_net_edge_bps == Decimal("35")


def test_higher_cost_stress_can_flip_an_otherwise_actionable_candidate() -> None:
    base_model = CostModel(
        maker_fee_bps=Decimal("1"),
        taker_fee_bps=Decimal("2"),
        entry_slippage_bps=Decimal("3"),
        exit_slippage_bps=Decimal("4"),
        funding_bps_per_interval=Decimal("0"),
        spread_bps=Decimal("2"),
    )
    base = evaluate_cost_aware_admission(
        _candidate(
            costs=_resolved_costs(model=base_model),
            gross="35",
            carry_cost="0",
            liquidity_penalty="2",
            uncertainty="10",
            funding_intervals=0,
        )
    )
    stressed = evaluate_cost_aware_admission(
        _candidate(
            costs=_resolved_costs(model=base_model.stressed(Decimal("2"))),
            gross="35",
            carry_cost="0",
            liquidity_penalty="2",
            uncertainty="10",
            funding_intervals=0,
        )
    )

    assert base.decision is RegimeGateDecision.ALLOW
    assert stressed.decision is RegimeGateDecision.BLOCK
    assert stressed.expected_net_edge_bps < base.expected_net_edge_bps


def test_ranking_excludes_no_signal_and_preserves_regime_priority() -> None:
    reduced_strategy = _strategy(family="BREAKOUT", version="breakout_v2")
    ranked = rank_cost_aware_candidates(
        (
            _candidate(key="allow-smaller", gross="70"),
            _candidate(
                key="reduce-larger",
                strategy=reduced_strategy,
                gate=_gate(reduced_strategy, decision=RegimeGateDecision.REDUCE),
                gross="200",
            ),
            _candidate(key="allow-larger", gross="90"),
            _candidate(key="blocked", gross="20"),
        )
    )

    assert [item.candidate_key for item in ranked] == [
        "allow-larger",
        "allow-smaller",
        "reduce-larger",
    ]
    assert all(item.decision is not RegimeGateDecision.BLOCK for item in ranked)


def test_cost_layer_does_not_emit_risk_or_execution_instructions() -> None:
    result = evaluate_cost_aware_admission(_candidate())

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


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("gross", "-1"),
        ("carry_cost", "-1"),
        ("liquidity_penalty", "-1"),
        ("uncertainty", "-1"),
    ),
)
def test_negative_bps_inputs_are_rejected(field: str, value: str) -> None:
    kwargs = {field: value}
    with pytest.raises(ValueError):
        _candidate(**kwargs)
