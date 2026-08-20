from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.admission.cost_aware_v1 import CostAwareAdmissionResult
from app.ensemble.meta_score_v1 import (
    EnsembleCandidateInput,
    evaluate_ensemble_meta_score,
    rank_ensemble_candidates,
)
from app.experiments.evaluator import PairedEvaluationResult
from app.measurement.report import MeasurementDataset
from app.regime.strategy_gate_v1 import RegimeGateDecision


AT = datetime(2026, 8, 20, 20, 30, tzinfo=UTC)


def _cost(
    *,
    key: str = "BTCUSDT:LONG",
    version: str = "momentum_v2",
    decision: RegimeGateDecision = RegimeGateDecision.ALLOW,
    regime_score: str = "0.80",
    surplus: str = "50",
) -> CostAwareAdmissionResult:
    edge_surplus = Decimal(surplus)
    return CostAwareAdmissionResult(
        cost_policy_version="cost_aware_admission_v1",
        candidate_key=key,
        strategy_family="MOMENTUM",
        strategy_version=version,
        decision=decision,
        raw_edge_score=Decimal("0.75"),
        regime_compatibility_score=Decimal(regime_score),
        venue="BYBIT",
        cost_source_ref="fixture:costs",
        expected_gross_edge_bps=Decimal("100"),
        expected_fee_bps=Decimal("4"),
        expected_slippage_bps=Decimal("7"),
        expected_spread_bps=Decimal("2"),
        expected_funding_cost_bps=Decimal("0"),
        expected_execution_cost_bps=Decimal("13"),
        expected_carry_cost_bps=Decimal("0"),
        liquidity_penalty_bps=Decimal("2"),
        expected_total_cost_bps=Decimal("15"),
        expected_net_edge_bps=edge_surplus + Decimal("10"),
        uncertainty_bps=Decimal("10"),
        edge_surplus_bps=edge_surplus,
        cost_survival_ratio=Decimal("0.85"),
        reasons=("NET_EDGE_ABOVE_UNCERTAINTY",),
    )


def _evaluation(
    *,
    candidate_version: str = "momentum_v2",
    usable: int = 60,
    sample_adequate: bool = True,
    expectancy_delta: float | None = 0.25,
    drawdown_delta: float | None = -0.20,
    calibration_delta: float | None = -0.10,
) -> PairedEvaluationResult:
    return PairedEvaluationResult(
        control_version="legacy_control_v1",
        candidate_version=candidate_version,
        dataset=MeasurementDataset.BACKTEST,
        paired_sample_size=usable,
        paired_usable_sample_size=usable,
        sample_adequate=sample_adequate,
        incremental_net_expectancy_r=expectancy_delta,
        incremental_max_drawdown_r=drawdown_delta,
        hit_rate_delta=0.05 if sample_adequate else None,
        calibration_delta=calibration_delta,
        opportunity_overlap=0.5,
        candidate_only_wins=4,
        candidate_only_losses=2,
        control_only_wins=2,
        control_only_losses=3,
        same_data_hash="a" * 64,
        cost_model_hash="b" * 64,
        measurement_report={},
    )


def _candidate(
    *,
    key: str = "BTCUSDT:LONG",
    cost: CostAwareAdmissionResult | None = None,
    evaluation: PairedEvaluationResult | None = None,
    stability: str = "0.90",
    evidence_at: datetime = AT,
    evaluated_at: datetime = AT,
) -> EnsembleCandidateInput:
    selected_cost = cost or _cost(key=key)
    return EnsembleCandidateInput(
        candidate_key=key,
        cost_admission=selected_cost,
        paired_evaluation=evaluation or _evaluation(
            candidate_version=selected_cost.strategy_version
        ),
        recent_stability_score=Decimal(stability),
        evidence_observed_at=evidence_at,
        evaluated_at=evaluated_at,
    )


def test_meta_score_is_multiplicative_evidence_weight_not_signal_average() -> None:
    result = evaluate_ensemble_meta_score(_candidate())

    assert result.policy_version == "evidence_weighted_meta_v1"
    assert result.oos_evidence_score == Decimal("0.583333")
    assert result.regime_score == Decimal("0.800000")
    assert result.calibration_score == Decimal("0.550000")
    assert result.recent_stability_score == Decimal("0.900000")
    assert result.sample_adequacy_score == Decimal("1.000000")
    assert result.evidence_weight == Decimal("0.231000")
    assert result.evidence_adjusted_edge_bps == Decimal("11.550000")
    assert result.admission_decision is RegimeGateDecision.ALLOW
    assert result.reasons == ("EVIDENCE_WEIGHTED",)


def test_single_low_evidence_strategy_is_not_normalized_to_full_weight() -> None:
    weak = _candidate(
        evaluation=_evaluation(
            usable=5,
            sample_adequate=False,
            expectancy_delta=None,
            drawdown_delta=None,
            calibration_delta=None,
        ),
        stability="0.50",
    )

    ranked = rank_ensemble_candidates((weak,))

    assert len(ranked) == 1
    assert ranked[0].sample_adequacy_score == Decimal("0.083333")
    assert ranked[0].evidence_weight < Decimal("0.02")
    assert ranked[0].evidence_weight != Decimal("1")
    assert "LOW_SAMPLE_SHRINKAGE" in ranked[0].reasons


def test_more_oos_evidence_increases_weight_without_changing_raw_cost_edge() -> None:
    low = evaluate_ensemble_meta_score(
        _candidate(evaluation=_evaluation(usable=20, sample_adequate=False))
    )
    high = evaluate_ensemble_meta_score(
        _candidate(evaluation=_evaluation(usable=60, sample_adequate=True))
    )

    assert high.evidence_weight > low.evidence_weight
    assert high.cost_edge_surplus_bps == low.cost_edge_surplus_bps == Decimal("50")
    assert high.evidence_adjusted_edge_bps > low.evidence_adjusted_edge_bps


def test_better_regime_fit_calibration_and_stability_each_raise_weight() -> None:
    base = evaluate_ensemble_meta_score(_candidate())
    worse_regime = evaluate_ensemble_meta_score(
        _candidate(cost=_cost(regime_score="0.40"))
    )
    worse_calibration = evaluate_ensemble_meta_score(
        _candidate(evaluation=_evaluation(calibration_delta=0.30))
    )
    worse_stability = evaluate_ensemble_meta_score(_candidate(stability="0.30"))

    assert base.evidence_weight > worse_regime.evidence_weight
    assert base.evidence_weight > worse_calibration.evidence_weight
    assert base.evidence_weight > worse_stability.evidence_weight


def test_positive_expectancy_and_lower_drawdown_both_matter_to_oos_evidence() -> None:
    strong = evaluate_ensemble_meta_score(_candidate())
    weaker_expectancy = evaluate_ensemble_meta_score(
        _candidate(evaluation=_evaluation(expectancy_delta=-0.25))
    )
    worse_drawdown = evaluate_ensemble_meta_score(
        _candidate(evaluation=_evaluation(drawdown_delta=0.40))
    )

    assert strong.oos_evidence_score > weaker_expectancy.oos_evidence_score
    assert strong.oos_evidence_score > worse_drawdown.oos_evidence_score


def test_insufficient_sample_uses_neutral_missing_deltas_but_strong_shrinkage() -> None:
    result = evaluate_ensemble_meta_score(
        _candidate(
            evaluation=_evaluation(
                usable=10,
                sample_adequate=False,
                expectancy_delta=None,
                drawdown_delta=None,
                calibration_delta=None,
            )
        )
    )

    assert result.oos_evidence_score == Decimal("0.500000")
    assert result.calibration_score == Decimal("0.500000")
    assert result.sample_adequacy_score == Decimal("0.166667")
    assert "LOW_SAMPLE_SHRINKAGE" in result.reasons


def test_internally_incomplete_adequate_oos_evidence_fails_closed() -> None:
    result = evaluate_ensemble_meta_score(
        _candidate(
            evaluation=_evaluation(
                usable=60,
                sample_adequate=True,
                expectancy_delta=None,
            )
        )
    )

    assert result.evidence_weight == Decimal("0")
    assert result.evidence_adjusted_edge_bps == Decimal("0")
    assert result.reasons == ("OOS_EVIDENCE_INCOMPLETE",)


def test_cost_block_is_monotonic_and_cannot_be_rescued_by_good_evidence() -> None:
    result = evaluate_ensemble_meta_score(
        _candidate(cost=_cost(decision=RegimeGateDecision.BLOCK, surplus="500"))
    )

    assert result.evidence_weight == Decimal("0")
    assert result.evidence_adjusted_edge_bps == Decimal("0")
    assert result.reasons == ("ADMISSION_BLOCKED",)


def test_cost_reduce_remains_reduce_even_with_strong_evidence() -> None:
    result = evaluate_ensemble_meta_score(
        _candidate(cost=_cost(decision=RegimeGateDecision.REDUCE))
    )

    assert result.admission_decision is RegimeGateDecision.REDUCE
    assert result.evidence_adjusted_edge_bps > 0


def test_candidate_version_and_cost_policy_mismatch_fail_closed() -> None:
    version_mismatch = evaluate_ensemble_meta_score(
        _candidate(evaluation=_evaluation(candidate_version="breakout_v2"))
    )
    bad_policy = _cost()
    bad_policy = CostAwareAdmissionResult(
        **{
            **{field: getattr(bad_policy, field) for field in bad_policy.__dataclass_fields__},
            "cost_policy_version": "cost_aware_admission_v999",
        }
    )
    policy_mismatch = evaluate_ensemble_meta_score(_candidate(cost=bad_policy))

    assert version_mismatch.reasons == ("EVIDENCE_VERSION_MISMATCH",)
    assert version_mismatch.evidence_weight == Decimal("0")
    assert policy_mismatch.reasons == ("UNSUPPORTED_COST_POLICY",)
    assert policy_mismatch.evidence_weight == Decimal("0")


def test_future_evidence_fails_closed() -> None:
    result = evaluate_ensemble_meta_score(
        _candidate(evidence_at=AT + timedelta(seconds=1))
    )

    assert result.evidence_weight == Decimal("0")
    assert result.reasons == ("EVIDENCE_FROM_FUTURE",)


def test_ranking_uses_evidence_adjusted_edge_without_renormalizing_weights() -> None:
    strong_evidence_small_edge = _candidate(
        key="strong",
        cost=_cost(key="strong", surplus="45"),
    )
    weak_evidence_large_edge = _candidate(
        key="weak",
        cost=_cost(key="weak", surplus="100"),
        evaluation=_evaluation(
            usable=8,
            sample_adequate=False,
            expectancy_delta=None,
            drawdown_delta=None,
            calibration_delta=None,
        ),
        stability="0.50",
    )

    ranked = rank_ensemble_candidates((weak_evidence_large_edge, strong_evidence_small_edge))

    assert [item.candidate_key for item in ranked] == ["strong", "weak"]
    assert ranked[0].evidence_weight > ranked[1].evidence_weight


def test_allow_candidates_rank_before_reduce_candidates() -> None:
    reduced = _candidate(
        key="reduce",
        cost=_cost(
            key="reduce",
            decision=RegimeGateDecision.REDUCE,
            surplus="500",
        ),
    )
    allowed = _candidate(
        key="allow",
        cost=_cost(key="allow", decision=RegimeGateDecision.ALLOW, surplus="20"),
    )

    ranked = rank_ensemble_candidates((reduced, allowed))

    assert [item.candidate_key for item in ranked] == ["allow", "reduce"]


def test_meta_layer_emits_no_risk_or_execution_instructions() -> None:
    result = evaluate_ensemble_meta_score(_candidate())

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


@pytest.mark.parametrize("field", ("recent_stability_score",))
def test_unit_interval_inputs_are_validated(field: str) -> None:
    kwargs = {field: Decimal("1.1")}
    with pytest.raises(ValueError):
        EnsembleCandidateInput(
            candidate_key="bad",
            cost_admission=_cost(key="bad"),
            paired_evaluation=_evaluation(),
            evidence_observed_at=AT,
            evaluated_at=AT,
            **kwargs,
        )
