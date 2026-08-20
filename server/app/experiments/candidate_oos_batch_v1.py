"""R4 candidate out-of-sample evidence batch versus frozen legacy control.

This module is an offline proof gate.  It orchestrates existing leakage-resistant
research infrastructure rather than inventing new statistics:

* SAI-008 ``WalkForwardFold`` proves which opportunities are OOS test samples;
* SAI-009 ``SelectionEvidence`` proves the parameter search was predeclared and
  terminal instead of hiding losing/failed variants;
* SAI-011/012 ``evaluate_paired`` proves control and candidate saw exactly the
  same opportunity, market snapshot, cost model, venue and regime, and returns
  the canonical incremental control delta.

A PASS is only evidence that may make a candidate batch eligible for the next
Shadow slice.  This module does not write promotion events, change execution
mode, size risk or activate LIVE trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Sequence

from ..backtest.multiple_testing import SelectionEvidence
from ..backtest.walk_forward import WalkForwardFold
from ..measurement.report import MeasurementDataset
from .evaluator import ArmObservation, PairedEvaluationResult, evaluate_paired

FROZEN_CONTROL_VERSION = "legacy_control_v1"
R4_CANDIDATE_VERSIONS = (
    "momentum_v2",
    "mean_reversion_v1",
    "crypto_carry_v1",
    "breakout_v2",
)
_POLICY_VERSION = "candidate_oos_batch_v1"
_ZERO = Decimal("0")
_ONE = Decimal("1")


class CandidateOosStatus(StrEnum):
    PASS_EVIDENCE = "PASS_EVIDENCE"
    FAIL_EVIDENCE = "FAIL_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"


@dataclass(frozen=True, slots=True)
class CandidateOosAcceptancePolicy:
    """Explicit research thresholds; no hidden production magic constants."""

    min_paired_usable_sample: int
    min_incremental_net_expectancy_r: Decimal
    max_incremental_max_drawdown_r: Decimal
    min_hit_rate_delta: Decimal
    max_calibration_delta: Decimal
    min_opportunity_overlap: Decimal
    min_distinct_regimes: int
    min_distinct_instruments: int

    def __post_init__(self) -> None:
        if isinstance(self.min_paired_usable_sample, bool) or not isinstance(
            self.min_paired_usable_sample, int
        ):
            raise ValueError("min_paired_usable_sample must be int")
        if self.min_paired_usable_sample < 1:
            raise ValueError("min_paired_usable_sample must be positive")
        for label in (
            "min_incremental_net_expectancy_r",
            "max_incremental_max_drawdown_r",
            "min_hit_rate_delta",
            "max_calibration_delta",
        ):
            _require_finite_decimal(label, getattr(self, label))
        _require_unit_decimal("min_opportunity_overlap", self.min_opportunity_overlap)
        for label in ("min_distinct_regimes", "min_distinct_instruments"):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{label} must be a positive integer")


@dataclass(frozen=True, slots=True)
class CandidateOosExperiment:
    """One strategy candidate's immutable OOS comparison inputs."""

    candidate_version: str
    control_rows: tuple[ArmObservation, ...]
    candidate_rows: tuple[ArmObservation, ...]
    oos_folds: tuple[WalkForwardFold, ...]
    selection_evidence: SelectionEvidence

    def __post_init__(self) -> None:
        _require_text("candidate_version", self.candidate_version)
        if self.candidate_version == FROZEN_CONTROL_VERSION:
            raise ValueError("candidate_version cannot be frozen control")
        if any(not isinstance(row, ArmObservation) for row in self.control_rows):
            raise ValueError("control_rows must contain ArmObservation values")
        if any(not isinstance(row, ArmObservation) for row in self.candidate_rows):
            raise ValueError("candidate_rows must contain ArmObservation values")
        if not self.oos_folds or any(
            not isinstance(fold, WalkForwardFold) for fold in self.oos_folds
        ):
            raise ValueError("oos_folds must contain at least one WalkForwardFold")
        if not isinstance(self.selection_evidence, SelectionEvidence):
            raise ValueError("selection_evidence must be SelectionEvidence")


@dataclass(frozen=True, slots=True)
class CandidateOosResult:
    policy_version: str
    candidate_version: str
    status: CandidateOosStatus
    reasons: tuple[str, ...]
    dataset_snapshot_id: str
    selection_context: str
    distinct_regimes: int
    distinct_instruments: int
    evaluation: PairedEvaluationResult | None

    def __post_init__(self) -> None:
        _require_text("policy_version", self.policy_version)
        _require_text("candidate_version", self.candidate_version)
        if not isinstance(self.status, CandidateOosStatus):
            raise ValueError("status must be CandidateOosStatus")
        if not self.reasons or any(not reason.strip() for reason in self.reasons):
            raise ValueError("reasons must contain non-blank reason codes")
        _sha256_identity("dataset_snapshot_id", self.dataset_snapshot_id)
        _require_text("selection_context", self.selection_context)
        if self.distinct_regimes < 0 or self.distinct_instruments < 0:
            raise ValueError("coverage counts must be non-negative")
        if self.evaluation is not None and not isinstance(
            self.evaluation, PairedEvaluationResult
        ):
            raise ValueError("evaluation must be PairedEvaluationResult or None")


@dataclass(frozen=True, slots=True)
class CandidateOosBatchResult:
    policy_version: str
    control_version: str
    required_candidate_versions: tuple[str, ...]
    results: tuple[CandidateOosResult, ...]
    missing_candidate_versions: tuple[str, ...]
    dataset_snapshot_id: str | None
    cost_model_hash: str | None
    eligible_for_shadow: bool

    def __post_init__(self) -> None:
        _require_text("policy_version", self.policy_version)
        if self.control_version != FROZEN_CONTROL_VERSION:
            raise ValueError("batch control must be frozen legacy_control_v1")
        if self.dataset_snapshot_id is not None:
            _sha256_identity("dataset_snapshot_id", self.dataset_snapshot_id)
        if self.cost_model_hash is not None:
            _sha256_identity("cost_model_hash", self.cost_model_hash)
        if not isinstance(self.eligible_for_shadow, bool):
            raise ValueError("eligible_for_shadow must be bool")


def run_candidate_oos_batch(
    experiments: Sequence[CandidateOosExperiment],
    *,
    policy: CandidateOosAcceptancePolicy,
    required_candidate_versions: tuple[str, ...] = R4_CANDIDATE_VERSIONS,
    control_version: str = FROZEN_CONTROL_VERSION,
) -> CandidateOosBatchResult:
    """Evaluate a deterministic R4 OOS batch without performing promotion."""

    if control_version != FROZEN_CONTROL_VERSION:
        raise ValueError("SAI-063 control must remain frozen legacy_control_v1")
    if not isinstance(policy, CandidateOosAcceptancePolicy):
        raise ValueError("policy must be CandidateOosAcceptancePolicy")
    _validate_required_versions(required_candidate_versions)

    experiment_list = list(experiments)
    if any(not isinstance(item, CandidateOosExperiment) for item in experiment_list):
        raise ValueError("experiments must contain CandidateOosExperiment values")
    versions = [item.candidate_version for item in experiment_list]
    if len(versions) != len(set(versions)):
        raise ValueError("duplicate candidate experiment version")

    # All candidate searches in this batch must identify the same immutable
    # research dataset snapshot. Different eligible opportunity universes may
    # still arise by strategy, but they cannot come from different data vintages.
    snapshot_ids = {
        item.selection_evidence.dataset_snapshot_id for item in experiment_list
    }
    for snapshot_id in snapshot_ids:
        _sha256_identity("dataset_snapshot_id", snapshot_id)
    if len(snapshot_ids) > 1:
        raise ValueError("candidate OOS batch requires one immutable dataset snapshot")
    dataset_snapshot_id = next(iter(snapshot_ids)) if snapshot_ids else None

    by_version = {item.candidate_version: item for item in experiment_list}
    missing = tuple(
        version for version in required_candidate_versions if version not in by_version
    )

    ordered_versions = tuple(
        version for version in required_candidate_versions if version in by_version
    ) + tuple(
        version for version in versions if version not in required_candidate_versions
    )
    results = tuple(
        _evaluate_experiment(by_version[version], policy=policy, control_version=control_version)
        for version in ordered_versions
    )

    cost_hashes = {
        result.evaluation.cost_model_hash
        for result in results
        if result.evaluation is not None
    }
    if len(cost_hashes) > 1:
        raise ValueError("candidate OOS batch requires one cost model hash")
    cost_model_hash = next(iter(cost_hashes)) if cost_hashes else None

    result_by_version = {item.candidate_version: item for item in results}
    eligible = not missing and all(
        result_by_version[version].status is CandidateOosStatus.PASS_EVIDENCE
        for version in required_candidate_versions
    )

    return CandidateOosBatchResult(
        policy_version=_POLICY_VERSION,
        control_version=control_version,
        required_candidate_versions=required_candidate_versions,
        results=results,
        missing_candidate_versions=missing,
        dataset_snapshot_id=dataset_snapshot_id,
        cost_model_hash=cost_model_hash,
        eligible_for_shadow=eligible,
    )


def _evaluate_experiment(
    experiment: CandidateOosExperiment,
    *,
    policy: CandidateOosAcceptancePolicy,
    control_version: str,
) -> CandidateOosResult:
    selection = experiment.selection_evidence
    _sha256_identity("dataset_snapshot_id", selection.dataset_snapshot_id)

    if selection.strategy_version != experiment.candidate_version:
        return _invalid(experiment, "RESEARCH_VERSION_MISMATCH")
    if not selection.promotion_ready or selection.blockers:
        return _invalid(experiment, "MULTIPLE_TESTING_EVIDENCE_INCOMPLETE")

    _validate_oos_universe(experiment)

    evaluation = evaluate_paired(
        experiment.control_rows,
        experiment.candidate_rows,
        control_version=control_version,
        candidate_version=experiment.candidate_version,
        dataset=MeasurementDataset.BACKTEST,
        min_sample=policy.min_paired_usable_sample,
    )

    regimes = {row.regime for row in experiment.control_rows}
    instruments = {row.instrument_id for row in experiment.control_rows}
    insufficient: list[str] = []
    if not evaluation.sample_adequate:
        insufficient.append("PAIRED_SAMPLE_INSUFFICIENT")
    if len(regimes) < policy.min_distinct_regimes:
        insufficient.append("REGIME_COVERAGE_INSUFFICIENT")
    if len(instruments) < policy.min_distinct_instruments:
        insufficient.append("INSTRUMENT_COVERAGE_INSUFFICIENT")
    if insufficient:
        return _result(
            experiment,
            status=CandidateOosStatus.INSUFFICIENT_EVIDENCE,
            reasons=tuple(insufficient),
            evaluation=evaluation,
            distinct_regimes=len(regimes),
            distinct_instruments=len(instruments),
        )

    # sample_adequate from the canonical evaluator guarantees these deltas are
    # populated; nevertheless fail closed if that invariant is ever violated.
    metrics = (
        evaluation.incremental_net_expectancy_r,
        evaluation.incremental_max_drawdown_r,
        evaluation.hit_rate_delta,
        evaluation.calibration_delta,
    )
    if any(value is None for value in metrics):
        return _result(
            experiment,
            status=CandidateOosStatus.INVALID_EVIDENCE,
            reasons=("PAIRED_EVALUATION_INCOMPLETE",),
            evaluation=evaluation,
            distinct_regimes=len(regimes),
            distinct_instruments=len(instruments),
        )

    failures: list[str] = []
    if Decimal(str(evaluation.incremental_net_expectancy_r)) < policy.min_incremental_net_expectancy_r:
        failures.append("INCREMENTAL_EXPECTANCY_BELOW_THRESHOLD")
    if Decimal(str(evaluation.incremental_max_drawdown_r)) > policy.max_incremental_max_drawdown_r:
        failures.append("DRAWDOWN_DETERIORATION_ABOVE_THRESHOLD")
    if Decimal(str(evaluation.hit_rate_delta)) < policy.min_hit_rate_delta:
        failures.append("HIT_RATE_DELTA_BELOW_THRESHOLD")
    if Decimal(str(evaluation.calibration_delta)) > policy.max_calibration_delta:
        failures.append("CALIBRATION_DETERIORATION_ABOVE_THRESHOLD")
    if Decimal(str(evaluation.opportunity_overlap)) < policy.min_opportunity_overlap:
        failures.append("OPPORTUNITY_OVERLAP_BELOW_THRESHOLD")

    return _result(
        experiment,
        status=(
            CandidateOosStatus.FAIL_EVIDENCE
            if failures
            else CandidateOosStatus.PASS_EVIDENCE
        ),
        reasons=tuple(failures or ("OOS_POLICY_PASSED",)),
        evaluation=evaluation,
        distinct_regimes=len(regimes),
        distinct_instruments=len(instruments),
    )


def _validate_oos_universe(experiment: CandidateOosExperiment) -> None:
    test_samples: dict[str, object] = {}
    for fold in experiment.oos_folds:
        for sample in fold.test:
            if sample.sample_id in test_samples:
                raise ValueError("overlapping OOS test sample ids would be double-counted")
            test_samples[sample.sample_id] = sample

    control = {row.opportunity_id: row for row in experiment.control_rows}
    candidate = {row.opportunity_id: row for row in experiment.candidate_rows}
    if len(control) != len(experiment.control_rows) or len(candidate) != len(
        experiment.candidate_rows
    ):
        raise ValueError("duplicate OOS opportunity identity")
    expected = set(test_samples)
    if set(control) != expected or set(candidate) != expected:
        raise ValueError("candidate/control rows must equal walk-forward OOS test universe")

    for sample_id, sample_object in test_samples.items():
        # Imported WalkForwardFold guarantees TimedSample values. Attribute use
        # here keeps the batch coupled to the splitter's point-in-time identity
        # without duplicating its dataclass type checks.
        observed_at = sample_object.observed_at  # type: ignore[attr-defined]
        if control[sample_id].decision_at != observed_at:
            raise ValueError("control decision timestamp must equal OOS test sample time")
        if candidate[sample_id].decision_at != observed_at:
            raise ValueError("candidate decision timestamp must equal OOS test sample time")


def _invalid(experiment: CandidateOosExperiment, reason: str) -> CandidateOosResult:
    return _result(
        experiment,
        status=CandidateOosStatus.INVALID_EVIDENCE,
        reasons=(reason,),
        evaluation=None,
        distinct_regimes=0,
        distinct_instruments=0,
    )


def _result(
    experiment: CandidateOosExperiment,
    *,
    status: CandidateOosStatus,
    reasons: tuple[str, ...],
    evaluation: PairedEvaluationResult | None,
    distinct_regimes: int,
    distinct_instruments: int,
) -> CandidateOosResult:
    selection = experiment.selection_evidence
    return CandidateOosResult(
        policy_version=_POLICY_VERSION,
        candidate_version=experiment.candidate_version,
        status=status,
        reasons=reasons,
        dataset_snapshot_id=selection.dataset_snapshot_id,
        selection_context=selection.selection_context,
        distinct_regimes=distinct_regimes,
        distinct_instruments=distinct_instruments,
        evaluation=evaluation,
    )


def _validate_required_versions(values: tuple[str, ...]) -> None:
    if not values:
        raise ValueError("required_candidate_versions must not be empty")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("required_candidate_versions must be non-blank strings")
    if len(values) != len(set(values)):
        raise ValueError("required_candidate_versions must be unique")
    if FROZEN_CONTROL_VERSION in values:
        raise ValueError("frozen control cannot be listed as candidate")


def _sha256_identity(label: str, value: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdefABCDEF" for char in value):
        raise ValueError(f"{label} must be a 64-character SHA-256 identity")


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")


def _require_finite_decimal(label: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{label} must be a finite Decimal")


def _require_unit_decimal(label: str, value: Decimal) -> None:
    _require_finite_decimal(label, value)
    if not _ZERO <= value <= _ONE:
        raise ValueError(f"{label} must be between 0 and 1")


__all__ = [
    "CandidateOosAcceptancePolicy",
    "CandidateOosBatchResult",
    "CandidateOosExperiment",
    "CandidateOosResult",
    "CandidateOosStatus",
    "FROZEN_CONTROL_VERSION",
    "R4_CANDIDATE_VERSIONS",
    "run_candidate_oos_batch",
]
