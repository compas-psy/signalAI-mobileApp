"""Cost-segmented rolling Paper A/B measurement for R4 candidates.

This module is deliberately statistical only.  It does not create owner
``PaperTrade`` rows, size positions, submit orders, promote strategies or
change execution mode.  It validates immutable arm evidence and delegates all
paired performance statistics to the canonical SAI-012 evaluator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from ..measurement.report import MeasurementDataset
from .evaluator import ArmObservation, PairedEvaluationResult, evaluate_paired

_POLICY_VERSION = "paper_ab_rolling_v1"


class PaperAbArmRole(StrEnum):
    CONTROL = "CONTROL"
    CANDIDATE = "CANDIDATE"


class PaperAbEvidenceStatus(StrEnum):
    """Whether an arm has an honest outcome label at report time."""

    EVALUATED = "EVALUATED"
    PENDING = "PENDING"
    INPUT_UNAVAILABLE = "INPUT_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class PaperAbArmObservation:
    """One immutable Paper A/B arm decision/outcome.

    ``PENDING`` means the decision exists but its declared measurement horizon
    has not produced a usable exit yet. ``INPUT_UNAVAILABLE`` means the horizon
    is known but the inputs needed for an honest label are absent. Neither may
    be converted to a zero return; only an explicit EVALUATED no-signal is a
    real zero-return decision.
    """

    pair_key: str
    candidate_version: str
    arm_role: PaperAbArmRole
    strategy_version: str
    instrument_id: str
    venue: str
    regime: str
    decision_at: datetime
    market_snapshot_hash: str
    cost_model_hash: str
    signal_emitted: bool
    net_r: Decimal | None
    confidence: Decimal | None
    evidence_status: PaperAbEvidenceStatus
    reason_code: str | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("pair_key", self.pair_key),
            ("candidate_version", self.candidate_version),
            ("strategy_version", self.strategy_version),
            ("instrument_id", self.instrument_id),
            ("venue", self.venue),
            ("regime", self.regime),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if not isinstance(self.arm_role, PaperAbArmRole):
            raise ValueError("arm_role must be PaperAbArmRole")
        if not isinstance(self.evidence_status, PaperAbEvidenceStatus):
            raise ValueError("evidence_status must be PaperAbEvidenceStatus")
        _aware("decision_at", self.decision_at)
        _sha256("market_snapshot_hash", self.market_snapshot_hash)
        _sha256("cost_model_hash", self.cost_model_hash)
        if self.confidence is not None:
            if not isinstance(self.confidence, Decimal) or not self.confidence.is_finite():
                raise ValueError("confidence must be a finite Decimal")
            if not Decimal(0) <= self.confidence <= Decimal(1):
                raise ValueError("confidence must be between 0 and 1")
        if self.net_r is not None and (
            not isinstance(self.net_r, Decimal) or not self.net_r.is_finite()
        ):
            raise ValueError("net_r must be a finite Decimal")

        if self.evidence_status is PaperAbEvidenceStatus.EVALUATED:
            if self.reason_code is not None:
                raise ValueError("EVALUATED evidence must not have reason_code")
            if self.signal_emitted and self.net_r is None:
                raise ValueError("usable emitted signal requires net_r")
            if not self.signal_emitted and self.net_r is not None:
                raise ValueError("evaluated no-signal must not have net_r")
        elif self.evidence_status is PaperAbEvidenceStatus.PENDING:
            if self.net_r is not None:
                raise ValueError("PENDING evidence must not have net_r")
            if self.reason_code is not None:
                raise ValueError("PENDING evidence must not have reason_code")
        else:
            if self.net_r is not None:
                raise ValueError("INPUT_UNAVAILABLE evidence must not have net_r")
            if not isinstance(self.reason_code, str) or not self.reason_code.strip():
                raise ValueError("INPUT_UNAVAILABLE evidence requires reason_code")

    @property
    def label_usable(self) -> bool:
        return self.evidence_status is PaperAbEvidenceStatus.EVALUATED

    def to_canonical(self) -> ArmObservation:
        return ArmObservation(
            opportunity_id=self.pair_key,
            instrument_id=self.instrument_id,
            decision_at=self.decision_at,
            market_snapshot_hash=self.market_snapshot_hash,
            cost_model_hash=self.cost_model_hash,
            venue=self.venue,
            regime=self.regime,
            signal_emitted=self.signal_emitted,
            net_r=self.net_r,
            confidence=self.confidence,
            label_usable=self.label_usable,
        )


@dataclass(frozen=True, slots=True)
class PaperAbCostSegment:
    cost_model_hash: str
    pair_count: int
    usable_pair_count: int
    evaluation: PairedEvaluationResult | None


@dataclass(frozen=True, slots=True)
class PaperAbRollingReport:
    policy_version: str
    control_version: str
    candidate_version: str
    window_start: datetime
    window_end: datetime
    total_pairs: int
    total_usable_pairs: int
    segments: tuple[PaperAbCostSegment, ...]
    recommendation: str


def build_rolling_paper_report(
    control_rows: Iterable[PaperAbArmObservation],
    candidate_rows: Iterable[PaperAbArmObservation],
    *,
    control_version: str,
    candidate_version: str,
    as_of: datetime,
    window: timedelta,
    min_sample: int = 30,
) -> PaperAbRollingReport:
    """Build a rolling PAPER report without mixing incomparable cost regimes."""

    if not isinstance(control_version, str) or not control_version.strip():
        raise ValueError("control_version is required")
    if not isinstance(candidate_version, str) or not candidate_version.strip():
        raise ValueError("candidate_version is required")
    if control_version == candidate_version:
        raise ValueError("control and candidate versions must differ")
    _aware("as_of", as_of)
    if not isinstance(window, timedelta) or window <= timedelta(0):
        raise ValueError("window must be a positive timedelta")
    if min_sample < 1:
        raise ValueError("min_sample must be positive")

    end = as_of.astimezone(UTC)
    start = end - window
    control = _window_index(
        control_rows,
        expected_role=PaperAbArmRole.CONTROL,
        expected_strategy=control_version,
        expected_candidate=candidate_version,
        start=start,
        end=end,
    )
    candidate = _window_index(
        candidate_rows,
        expected_role=PaperAbArmRole.CANDIDATE,
        expected_strategy=candidate_version,
        expected_candidate=candidate_version,
        start=start,
        end=end,
    )

    if control.keys() != candidate.keys():
        raise ValueError("control and candidate must use same rolling pair universe")
    if not control:
        return PaperAbRollingReport(
            policy_version=_POLICY_VERSION,
            control_version=control_version,
            candidate_version=candidate_version,
            window_start=start,
            window_end=end,
            total_pairs=0,
            total_usable_pairs=0,
            segments=(),
            recommendation="MEASURE_ONLY",
        )

    by_cost: dict[str, list[str]] = {}
    total_usable = 0
    for key in sorted(control):
        left, right = control[key], candidate[key]
        _require_pair_context(left, right)
        cost = left.cost_model_hash.lower()
        by_cost.setdefault(cost, []).append(key)
        if left.label_usable and right.label_usable:
            total_usable += 1

    segments: list[PaperAbCostSegment] = []
    for cost_hash in sorted(by_cost):
        keys = by_cost[cost_hash]
        left = [control[key].to_canonical() for key in keys]
        right = [candidate[key].to_canonical() for key in keys]
        evaluation = evaluate_paired(
            left,
            right,
            control_version=control_version,
            candidate_version=candidate_version,
            dataset=MeasurementDataset.PAPER,
            min_sample=min_sample,
        )
        usable = sum(
            1
            for key in keys
            if control[key].label_usable and candidate[key].label_usable
        )
        segments.append(
            PaperAbCostSegment(
                cost_model_hash=cost_hash,
                pair_count=len(keys),
                usable_pair_count=usable,
                evaluation=evaluation,
            )
        )

    return PaperAbRollingReport(
        policy_version=_POLICY_VERSION,
        control_version=control_version,
        candidate_version=candidate_version,
        window_start=start,
        window_end=end,
        total_pairs=len(control),
        total_usable_pairs=total_usable,
        segments=tuple(segments),
        recommendation="MEASURE_ONLY",
    )


def _window_index(
    rows: Iterable[PaperAbArmObservation],
    *,
    expected_role: PaperAbArmRole,
    expected_strategy: str,
    expected_candidate: str,
    start: datetime,
    end: datetime,
) -> dict[str, PaperAbArmObservation]:
    result: dict[str, PaperAbArmObservation] = {}
    for row in rows:
        if not isinstance(row, PaperAbArmObservation):
            raise ValueError("Paper A/B rows must be PaperAbArmObservation")
        moment = row.decision_at.astimezone(UTC)
        if moment > end:
            raise ValueError(f"future Paper A/B observation: {row.pair_key}")
        if moment < start:
            continue
        if row.arm_role is not expected_role:
            raise ValueError(f"arm role mismatch for {row.pair_key}")
        if row.strategy_version != expected_strategy:
            raise ValueError(f"strategy version mismatch for {row.pair_key}")
        if row.candidate_version != expected_candidate:
            raise ValueError(f"candidate version mismatch for {row.pair_key}")
        if row.pair_key in result:
            raise ValueError(f"duplicate Paper A/B pair: {row.pair_key}")
        result[row.pair_key] = row
    return result


def _require_pair_context(
    control: PaperAbArmObservation,
    candidate: PaperAbArmObservation,
) -> None:
    if control.instrument_id != candidate.instrument_id:
        raise ValueError(f"instrument mismatch for {control.pair_key}")
    if control.decision_at.astimezone(UTC) != candidate.decision_at.astimezone(UTC):
        raise ValueError(f"decision timestamp mismatch for {control.pair_key}")
    if control.market_snapshot_hash.lower() != candidate.market_snapshot_hash.lower():
        raise ValueError(f"market snapshot mismatch for {control.pair_key}")
    if control.cost_model_hash.lower() != candidate.cost_model_hash.lower():
        raise ValueError(f"cost model mismatch for {control.pair_key}")
    if control.venue != candidate.venue:
        raise ValueError(f"venue mismatch for {control.pair_key}")
    if control.regime != candidate.regime:
        raise ValueError(f"regime mismatch for {control.pair_key}")


def _sha256(name: str, value: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdefABCDEF" for char in value
    ):
        raise ValueError(f"{name} must be a 64-character SHA-256 identity")


def _aware(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "PaperAbArmObservation",
    "PaperAbArmRole",
    "PaperAbCostSegment",
    "PaperAbEvidenceStatus",
    "PaperAbRollingReport",
    "build_rolling_paper_report",
]
