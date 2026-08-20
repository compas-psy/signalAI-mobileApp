"""Evidence-weighted meta scoring for the R4 candidate stack.

This layer does not average strategy votes into a consensus. Each candidate
keeps its own cost-aware edge in basis points and receives an absolute evidence
weight derived from OOS performance, current regime fit, calibration, recent
stability and sample adequacy. Weights are deliberately *not* normalized across
whatever candidates happen to be present.

Low-sample candidates therefore remain heavily shrunk instead of becoming 100%
credible merely because they are alone. This layer emits no risk sizing,
portfolio allocation or execution instructions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from ..admission.cost_aware_v1 import CostAwareAdmissionResult
from ..experiments.evaluator import PairedEvaluationResult
from ..regime.strategy_gate_v1 import RegimeGateDecision

_POLICY_VERSION = "evidence_weighted_meta_v1"
_SUPPORTED_COST_POLICY = "cost_aware_admission_v1"
_TARGET_SAMPLE = Decimal("60")
_ZERO = Decimal("0")
_HALF = Decimal("0.5")
_ONE = Decimal("1")
_QUANT = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class EnsembleCandidateInput:
    """One cost-aware candidate plus immutable evidence available as-of now."""

    candidate_key: str
    cost_admission: CostAwareAdmissionResult
    paired_evaluation: PairedEvaluationResult
    recent_stability_score: Decimal
    evidence_observed_at: datetime
    evaluated_at: datetime

    def __post_init__(self) -> None:
        _require_text("candidate_key", self.candidate_key)
        if not isinstance(self.cost_admission, CostAwareAdmissionResult):
            raise ValueError("cost_admission must be CostAwareAdmissionResult")
        if not isinstance(self.paired_evaluation, PairedEvaluationResult):
            raise ValueError("paired_evaluation must be PairedEvaluationResult")
        _require_unit_decimal("recent_stability_score", self.recent_stability_score)
        _require_aware_datetime("evidence_observed_at", self.evidence_observed_at)
        _require_aware_datetime("evaluated_at", self.evaluated_at)


@dataclass(frozen=True, slots=True)
class EnsembleMetaScoreResult:
    """Auditable evidence credibility applied to cost-aware edge only."""

    policy_version: str
    candidate_key: str
    strategy_family: str
    strategy_version: str
    admission_decision: RegimeGateDecision
    cost_edge_surplus_bps: Decimal
    oos_evidence_score: Decimal
    regime_score: Decimal
    calibration_score: Decimal
    recent_stability_score: Decimal
    sample_adequacy_score: Decimal
    evidence_weight: Decimal
    evidence_adjusted_edge_bps: Decimal
    paired_usable_sample_size: int
    sample_adequate: bool
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text("policy_version", self.policy_version)
        _require_text("candidate_key", self.candidate_key)
        _require_text("strategy_family", self.strategy_family)
        _require_text("strategy_version", self.strategy_version)
        if not isinstance(self.admission_decision, RegimeGateDecision):
            raise ValueError("admission_decision must be RegimeGateDecision")
        _require_finite_decimal("cost_edge_surplus_bps", self.cost_edge_surplus_bps)
        for label in (
            "oos_evidence_score",
            "regime_score",
            "calibration_score",
            "recent_stability_score",
            "sample_adequacy_score",
            "evidence_weight",
        ):
            _require_unit_decimal(label, getattr(self, label))
        _require_finite_decimal(
            "evidence_adjusted_edge_bps", self.evidence_adjusted_edge_bps
        )
        if isinstance(self.paired_usable_sample_size, bool) or not isinstance(
            self.paired_usable_sample_size, int
        ):
            raise ValueError("paired_usable_sample_size must be int")
        if self.paired_usable_sample_size < 0:
            raise ValueError("paired_usable_sample_size must be non-negative")
        if not isinstance(self.sample_adequate, bool):
            raise ValueError("sample_adequate must be bool")
        if not self.reasons or any(not reason.strip() for reason in self.reasons):
            raise ValueError("reasons must contain non-blank reason codes")


def evaluate_ensemble_meta_score(
    candidate: EnsembleCandidateInput,
) -> EnsembleMetaScoreResult:
    """Apply absolute evidence credibility to one already-admitted candidate."""

    if not isinstance(candidate, EnsembleCandidateInput):
        raise ValueError("candidate must be EnsembleCandidateInput")

    cost = candidate.cost_admission
    evidence = candidate.paired_evaluation

    if cost.cost_policy_version != _SUPPORTED_COST_POLICY:
        return _blocked(candidate, "UNSUPPORTED_COST_POLICY")
    if evidence.candidate_version != cost.strategy_version:
        return _blocked(candidate, "EVIDENCE_VERSION_MISMATCH")
    if candidate.evidence_observed_at > candidate.evaluated_at:
        return _blocked(candidate, "EVIDENCE_FROM_FUTURE")
    if cost.decision is RegimeGateDecision.BLOCK:
        return _blocked(candidate, "ADMISSION_BLOCKED")

    # If the evaluator itself says the paired sample is adequate, the core
    # statistical deltas must be populated. Missing values here are an internal
    # evidence integrity failure rather than permission to assume neutrality.
    if evidence.sample_adequate and any(
        value is None
        for value in (
            evidence.incremental_net_expectancy_r,
            evidence.incremental_max_drawdown_r,
            evidence.hit_rate_delta,
            evidence.calibration_delta,
        )
    ):
        return _blocked(candidate, "OOS_EVIDENCE_INCOMPLETE")

    oos_score = _oos_score(evidence)
    calibration_score = _calibration_score(evidence)
    regime_score = _quantize(_clamp(cost.regime_compatibility_score))
    stability_score = _quantize(_clamp(candidate.recent_stability_score))
    sample_score = _sample_adequacy_score(evidence.paired_usable_sample_size)

    evidence_weight = _quantize(
        oos_score
        * regime_score
        * calibration_score
        * stability_score
        * sample_score
    )
    adjusted_edge = _quantize(cost.edge_surplus_bps * evidence_weight)

    reasons = ["EVIDENCE_WEIGHTED"]
    if not evidence.sample_adequate:
        reasons.append("LOW_SAMPLE_SHRINKAGE")

    return EnsembleMetaScoreResult(
        policy_version=_POLICY_VERSION,
        candidate_key=candidate.candidate_key,
        strategy_family=cost.strategy_family,
        strategy_version=cost.strategy_version,
        admission_decision=cost.decision,
        cost_edge_surplus_bps=cost.edge_surplus_bps,
        oos_evidence_score=oos_score,
        regime_score=regime_score,
        calibration_score=calibration_score,
        recent_stability_score=stability_score,
        sample_adequacy_score=sample_score,
        evidence_weight=evidence_weight,
        evidence_adjusted_edge_bps=adjusted_edge,
        paired_usable_sample_size=evidence.paired_usable_sample_size,
        sample_adequate=evidence.sample_adequate,
        reasons=tuple(reasons),
    )


def rank_ensemble_candidates(
    candidates: Sequence[EnsembleCandidateInput],
) -> tuple[EnsembleMetaScoreResult, ...]:
    """Rank candidates without renormalizing evidence weights across the set."""

    evaluated = [evaluate_ensemble_meta_score(item) for item in candidates]
    actionable = [
        item
        for item in evaluated
        if item.admission_decision is not RegimeGateDecision.BLOCK
        and item.evidence_weight > _ZERO
    ]
    decision_rank = {
        RegimeGateDecision.ALLOW: 0,
        RegimeGateDecision.REDUCE: 1,
    }
    return tuple(
        sorted(
            actionable,
            key=lambda item: (
                decision_rank[item.admission_decision],
                -item.evidence_adjusted_edge_bps,
                -item.evidence_weight,
                -item.cost_edge_surplus_bps,
                item.candidate_key,
            ),
        )
    )


def _oos_score(evidence: PairedEvaluationResult) -> Decimal:
    # Missing statistical deltas are permitted only while sample is explicitly
    # insufficient. In that exploratory state they remain neutral (0.5) and the
    # separate sample term supplies the strong shrinkage.
    expectancy = _delta_score(
        evidence.incremental_net_expectancy_r,
        beneficial_when_positive=True,
    )
    drawdown = _delta_score(
        evidence.incremental_max_drawdown_r,
        beneficial_when_positive=False,
    )
    hit_rate = _delta_score(
        evidence.hit_rate_delta,
        beneficial_when_positive=True,
    )
    return _quantize((expectancy + drawdown + hit_rate) / Decimal("3"))


def _calibration_score(evidence: PairedEvaluationResult) -> Decimal:
    # SAI-012 calibration_delta is candidate_error - control_error, therefore a
    # negative delta is an improvement.
    return _quantize(
        _delta_score(evidence.calibration_delta, beneficial_when_positive=False)
    )


def _delta_score(value: float | None, *, beneficial_when_positive: bool) -> Decimal:
    if value is None:
        return _HALF
    delta = Decimal(str(value))
    signed = delta if beneficial_when_positive else -delta
    return _clamp(_HALF + signed / Decimal("2"))


def _sample_adequacy_score(usable_sample: int) -> Decimal:
    if isinstance(usable_sample, bool) or not isinstance(usable_sample, int):
        raise ValueError("paired_usable_sample_size must be int")
    if usable_sample < 0:
        raise ValueError("paired_usable_sample_size must be non-negative")
    return _quantize(min(Decimal(usable_sample) / _TARGET_SAMPLE, _ONE))


def _blocked(candidate: EnsembleCandidateInput, reason: str) -> EnsembleMetaScoreResult:
    cost = candidate.cost_admission
    evidence = candidate.paired_evaluation
    return EnsembleMetaScoreResult(
        policy_version=_POLICY_VERSION,
        candidate_key=candidate.candidate_key,
        strategy_family=cost.strategy_family,
        strategy_version=cost.strategy_version,
        admission_decision=RegimeGateDecision.BLOCK,
        cost_edge_surplus_bps=cost.edge_surplus_bps,
        oos_evidence_score=_ZERO,
        regime_score=_ZERO,
        calibration_score=_ZERO,
        recent_stability_score=candidate.recent_stability_score,
        sample_adequacy_score=_ZERO,
        evidence_weight=_ZERO,
        evidence_adjusted_edge_bps=_ZERO,
        paired_usable_sample_size=evidence.paired_usable_sample_size,
        sample_adequate=evidence.sample_adequate,
        reasons=(reason,),
    )


def _clamp(value: Decimal) -> Decimal:
    return min(max(value, _ZERO), _ONE)


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_QUANT, rounding=ROUND_HALF_UP)


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


def _require_aware_datetime(label: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


__all__ = [
    "EnsembleCandidateInput",
    "EnsembleMetaScoreResult",
    "evaluate_ensemble_meta_score",
    "rank_ensemble_candidates",
]
