from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtest.multiple_testing import SelectionEvidence
from app.backtest.walk_forward import TimedSample, WalkForwardFold
from app.experiments.candidate_oos_batch_v1 import (
    CandidateOosAcceptancePolicy,
    CandidateOosExperiment,
    CandidateOosStatus,
    R4_CANDIDATE_VERSIONS,
    run_candidate_oos_batch,
)
from app.experiments.evaluator import ArmObservation
from app.measurement.report import MeasurementDataset


BASE = datetime(2026, 1, 1, tzinfo=UTC)
MARKET_HASH = "a" * 64
COST_HASH = "b" * 64
SNAPSHOT_HASH = "c" * 64


def _sample(sample_id: str, day: int) -> TimedSample:
    observed = BASE + timedelta(days=day)
    return TimedSample(
        sample_id=sample_id,
        observed_at=observed,
        label_end_at=observed + timedelta(hours=12),
        market_segment="OOS",
    )


def _fold(ids: tuple[str, ...]) -> WalkForwardFold:
    train = (_sample("train", 0),)
    validation = (_sample("validation", 1),)
    test = tuple(_sample(sample_id, 10 + idx) for idx, sample_id in enumerate(ids))
    return WalkForwardFold(
        fold_index=0,
        train_start=BASE,
        train_end=BASE + timedelta(days=1),
        validation_start=BASE + timedelta(days=1),
        validation_end=BASE + timedelta(days=2),
        test_start=BASE + timedelta(days=10),
        test_end=BASE + timedelta(days=10 + len(ids) + 1),
        train=train,
        validation=validation,
        test=test,
        purged_sample_ids=(),
        embargoed_sample_ids=(),
        invalid_segment_sample_ids=(),
    )


def _row(
    opportunity_id: str,
    day: int,
    *,
    net_r: str,
    confidence: str,
    instrument: str = "BYBIT:BTCUSDT",
    regime: str = "TREND",
    market_hash: str = MARKET_HASH,
    cost_hash: str = COST_HASH,
) -> ArmObservation:
    return ArmObservation(
        opportunity_id=opportunity_id,
        instrument_id=instrument,
        decision_at=BASE + timedelta(days=10 + day),
        market_snapshot_hash=market_hash,
        cost_model_hash=cost_hash,
        venue="BYBIT" if instrument.startswith("BYBIT") else "MOEX",
        regime=regime,
        signal_emitted=True,
        net_r=Decimal(net_r),
        confidence=Decimal(confidence),
        label_usable=True,
    )


def _selection(candidate_version: str, *, ready: bool = True) -> SelectionEvidence:
    return SelectionEvidence(
        campaign_id=uuid.uuid4(),
        hypothesis_id=f"hypothesis:{candidate_version}",
        dataset_name="r4-oos",
        dataset_snapshot_id=SNAPSHOT_HASH,
        strategy_family="R4_CANDIDATE",
        strategy_version=candidate_version,
        planned_variants=3,
        registered_variants=3 if ready else 2,
        terminal_variants=3 if ready else 1,
        completed_variants=3 if ready else 1,
        failed_variants=0,
        best_trial_id=uuid.uuid4(),
        best_primary_metric=Decimal("0.1"),
        selection_context="best_of_3_registered_variants",
        blockers=() if ready else ("research campaign incomplete",),
        promotion_ready=ready,
    )


def _experiment(
    candidate_version: str,
    *,
    count: int = 6,
    candidate_returns: tuple[str, ...] | None = None,
    control_returns: tuple[str, ...] | None = None,
    ready: bool = True,
    diversify_context: bool = True,
) -> CandidateOosExperiment:
    ids = tuple(f"{candidate_version}:{index}" for index in range(count))
    candidate_values = candidate_returns or tuple("0.30" for _ in ids)
    control_values = control_returns or tuple("0.10" for _ in ids)
    if len(candidate_values) != count or len(control_values) != count:
        raise ValueError("fixture return lengths must equal count")

    control_rows: list[ArmObservation] = []
    candidate_rows: list[ArmObservation] = []
    for index, opportunity_id in enumerate(ids):
        if diversify_context:
            instrument = "BYBIT:BTCUSDT" if index % 2 == 0 else "MOEX:SiU6"
            regime = "TREND" if index % 2 == 0 else "RANGE"
        else:
            instrument = "BYBIT:BTCUSDT"
            regime = "TREND"
        control_rows.append(
            _row(
                opportunity_id,
                index,
                net_r=control_values[index],
                confidence="0.60",
                instrument=instrument,
                regime=regime,
            )
        )
        candidate_rows.append(
            _row(
                opportunity_id,
                index,
                net_r=candidate_values[index],
                confidence="0.80",
                instrument=instrument,
                regime=regime,
            )
        )

    return CandidateOosExperiment(
        candidate_version=candidate_version,
        control_rows=tuple(control_rows),
        candidate_rows=tuple(candidate_rows),
        oos_folds=(_fold(ids),),
        selection_evidence=_selection(candidate_version, ready=ready),
    )


def _policy(*, min_sample: int = 5) -> CandidateOosAcceptancePolicy:
    return CandidateOosAcceptancePolicy(
        min_paired_usable_sample=min_sample,
        min_incremental_net_expectancy_r=Decimal("0.05"),
        max_incremental_max_drawdown_r=Decimal("0"),
        min_hit_rate_delta=Decimal("-0.10"),
        max_calibration_delta=Decimal("0.05"),
        min_opportunity_overlap=Decimal("0.50"),
        min_distinct_regimes=2,
        min_distinct_instruments=2,
    )


def test_complete_r4_batch_can_recommend_shadow_only_when_every_candidate_passes() -> None:
    experiments = tuple(_experiment(version) for version in R4_CANDIDATE_VERSIONS)

    batch = run_candidate_oos_batch(experiments, policy=_policy())

    assert batch.control_version == "legacy_control_v1"
    assert batch.required_candidate_versions == R4_CANDIDATE_VERSIONS
    assert batch.missing_candidate_versions == ()
    assert batch.eligible_for_shadow is True
    assert [item.candidate_version for item in batch.results] == list(R4_CANDIDATE_VERSIONS)
    assert all(item.status is CandidateOosStatus.PASS_EVIDENCE for item in batch.results)
    assert all(item.evaluation.sample_adequate for item in batch.results)
    assert all(item.evaluation.incremental_net_expectancy_r == pytest.approx(0.20) for item in batch.results)


def test_batch_is_not_shadow_eligible_when_any_required_candidate_is_missing() -> None:
    experiments = tuple(_experiment(version) for version in R4_CANDIDATE_VERSIONS[:-1])

    batch = run_candidate_oos_batch(experiments, policy=_policy())

    assert batch.eligible_for_shadow is False
    assert batch.missing_candidate_versions == (R4_CANDIDATE_VERSIONS[-1],)


def test_insufficient_sample_is_not_a_pass() -> None:
    experiment = _experiment("momentum_v2", count=3)

    batch = run_candidate_oos_batch(
        (experiment,),
        policy=_policy(min_sample=5),
        required_candidate_versions=("momentum_v2",),
    )

    result = batch.results[0]
    assert result.status is CandidateOosStatus.INSUFFICIENT_EVIDENCE
    assert "PAIRED_SAMPLE_INSUFFICIENT" in result.reasons
    assert batch.eligible_for_shadow is False


def test_positive_signal_count_is_not_enough_when_incremental_expectancy_fails() -> None:
    experiment = _experiment(
        "momentum_v2",
        candidate_returns=tuple("0.05" for _ in range(6)),
        control_returns=tuple("0.10" for _ in range(6)),
    )

    result = run_candidate_oos_batch(
        (experiment,),
        policy=_policy(),
        required_candidate_versions=("momentum_v2",),
    ).results[0]

    assert result.status is CandidateOosStatus.FAIL_EVIDENCE
    assert "INCREMENTAL_EXPECTANCY_BELOW_THRESHOLD" in result.reasons


def test_drawdown_deterioration_can_fail_candidate_even_with_positive_mean_delta() -> None:
    experiment = _experiment(
        "momentum_v2",
        candidate_returns=("1.5", "-1.0", "0.4", "0.4", "0.4", "0.4"),
        control_returns=("0.1", "0.1", "0.1", "0.1", "0.1", "0.1"),
    )

    result = run_candidate_oos_batch(
        (experiment,),
        policy=_policy(),
        required_candidate_versions=("momentum_v2",),
    ).results[0]

    assert result.evaluation.incremental_net_expectancy_r > 0
    assert result.status is CandidateOosStatus.FAIL_EVIDENCE
    assert "DRAWDOWN_DETERIORATION_ABOVE_THRESHOLD" in result.reasons


def test_regime_and_instrument_coverage_are_required_for_oos_claim() -> None:
    experiment = _experiment("momentum_v2", diversify_context=False)

    result = run_candidate_oos_batch(
        (experiment,),
        policy=_policy(),
        required_candidate_versions=("momentum_v2",),
    ).results[0]

    assert result.status is CandidateOosStatus.INSUFFICIENT_EVIDENCE
    assert "REGIME_COVERAGE_INSUFFICIENT" in result.reasons
    assert "INSTRUMENT_COVERAGE_INSUFFICIENT" in result.reasons


def test_multiple_testing_campaign_must_be_complete_before_oos_pass_is_possible() -> None:
    experiment = _experiment("momentum_v2", ready=False)

    result = run_candidate_oos_batch(
        (experiment,),
        policy=_policy(),
        required_candidate_versions=("momentum_v2",),
    ).results[0]

    assert result.status is CandidateOosStatus.INVALID_EVIDENCE
    assert result.reasons == ("MULTIPLE_TESTING_EVIDENCE_INCOMPLETE",)


def test_only_frozen_legacy_control_is_allowed() -> None:
    with pytest.raises(ValueError, match="legacy_control_v1"):
        run_candidate_oos_batch(
            (_experiment("momentum_v2"),),
            policy=_policy(),
            required_candidate_versions=("momentum_v2",),
            control_version="legacy_control_v2",
        )


def test_candidate_rows_must_be_exact_walk_forward_test_universe() -> None:
    experiment = _experiment("momentum_v2")
    truncated = CandidateOosExperiment(
        candidate_version=experiment.candidate_version,
        control_rows=experiment.control_rows[:-1],
        candidate_rows=experiment.candidate_rows[:-1],
        oos_folds=experiment.oos_folds,
        selection_evidence=experiment.selection_evidence,
    )

    with pytest.raises(ValueError, match="walk-forward OOS test universe"):
        run_candidate_oos_batch(
            (truncated,),
            policy=_policy(),
            required_candidate_versions=("momentum_v2",),
        )


def test_overlapping_walk_forward_test_ids_are_rejected_not_double_counted() -> None:
    experiment = _experiment("momentum_v2")
    overlapping = CandidateOosExperiment(
        candidate_version=experiment.candidate_version,
        control_rows=experiment.control_rows,
        candidate_rows=experiment.candidate_rows,
        oos_folds=(experiment.oos_folds[0], experiment.oos_folds[0]),
        selection_evidence=experiment.selection_evidence,
    )

    with pytest.raises(ValueError, match="overlapping OOS test sample"):
        run_candidate_oos_batch(
            (overlapping,),
            policy=_policy(),
            required_candidate_versions=("momentum_v2",),
        )


def test_same_data_and_cost_integrity_is_delegated_to_strict_paired_evaluator() -> None:
    experiment = _experiment("momentum_v2")
    bad_candidate_rows = list(experiment.candidate_rows)
    original = bad_candidate_rows[0]
    bad_candidate_rows[0] = ArmObservation(
        opportunity_id=original.opportunity_id,
        instrument_id=original.instrument_id,
        decision_at=original.decision_at,
        market_snapshot_hash="d" * 64,
        cost_model_hash=original.cost_model_hash,
        venue=original.venue,
        regime=original.regime,
        signal_emitted=original.signal_emitted,
        net_r=original.net_r,
        confidence=original.confidence,
        label_usable=original.label_usable,
    )
    contaminated = CandidateOosExperiment(
        candidate_version=experiment.candidate_version,
        control_rows=experiment.control_rows,
        candidate_rows=tuple(bad_candidate_rows),
        oos_folds=experiment.oos_folds,
        selection_evidence=experiment.selection_evidence,
    )

    with pytest.raises(ValueError, match="market snapshot mismatch"):
        run_candidate_oos_batch(
            (contaminated,),
            policy=_policy(),
            required_candidate_versions=("momentum_v2",),
        )


def test_candidate_version_must_match_search_campaign_identity() -> None:
    experiment = _experiment("momentum_v2")
    mismatch = CandidateOosExperiment(
        candidate_version="momentum_v2",
        control_rows=experiment.control_rows,
        candidate_rows=experiment.candidate_rows,
        oos_folds=experiment.oos_folds,
        selection_evidence=_selection("breakout_v2"),
    )

    result = run_candidate_oos_batch(
        (mismatch,),
        policy=_policy(),
        required_candidate_versions=("momentum_v2",),
    ).results[0]

    assert result.status is CandidateOosStatus.INVALID_EVIDENCE
    assert result.reasons == ("RESEARCH_VERSION_MISMATCH",)


def test_batch_emits_recommendation_evidence_not_promotion_or_execution() -> None:
    batch = run_candidate_oos_batch(
        (_experiment("momentum_v2"),),
        policy=_policy(),
        required_candidate_versions=("momentum_v2",),
    )

    for forbidden in (
        "promote",
        "promotion_event",
        "execution_mode",
        "risk_amount",
        "quantity",
        "order_intent",
        "live_enabled",
    ):
        assert not hasattr(batch, forbidden)
        assert all(not hasattr(item, forbidden) for item in batch.results)
