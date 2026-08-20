"""Pre-approval portfolio conflict/correlation resolution for R4 candidates.

The resolver consumes SAI-061 evidence-weighted candidates plus an explicit
point-in-time snapshot of current portfolio exposure.  It decides which ideas
may proceed to the existing downstream risk-sizing engine.  It deliberately
owns no position size, quantity, leverage, stop, target or order semantics.

Correlation is interpreted as *position-risk* correlation:

    price_correlation × direction_sign(left) × direction_sign(right)

This avoids the common error of using ``abs(price_correlation)``.  For example,
two negatively correlated assets held in opposite directions can still express
nearly the same portfolio risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Sequence

from ..ensemble.meta_score_v1 import EnsembleMetaScoreResult
from ..models.enums import Direction
from ..regime.strategy_gate_v1 import RegimeGateDecision

_POLICY_VERSION = "portfolio_conflict_resolver_v1"
_SUPPORTED_META_POLICY = "evidence_weighted_meta_v1"
_ZERO = Decimal("0")
_ONE = Decimal("1")


class AssetClass(StrEnum):
    CRYPTO = "CRYPTO"
    FORTS = "FORTS"


class ResolutionStatus(StrEnum):
    SELECTED = "SELECTED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class RiskBucket:
    key: str
    open_risk_pct: Decimal

    def __post_init__(self) -> None:
        _require_text("risk bucket key", self.key)
        _require_non_negative_decimal("open_risk_pct", self.open_risk_pct)


@dataclass(frozen=True, slots=True)
class ExistingPortfolioExposure:
    exposure_key: str
    underlying_key: str
    direction: Direction
    venue: str
    asset_class: AssetClass
    correlation_cluster: str | None = None

    def __post_init__(self) -> None:
        _require_text("exposure_key", self.exposure_key)
        _require_text("underlying_key", self.underlying_key)
        if not isinstance(self.direction, Direction):
            raise ValueError("direction must be Direction")
        _require_text("venue", self.venue)
        if not isinstance(self.asset_class, AssetClass):
            raise ValueError("asset_class must be AssetClass")
        if self.correlation_cluster is not None:
            _require_text("correlation_cluster", self.correlation_cluster)


@dataclass(frozen=True, slots=True)
class PortfolioExposureSnapshot:
    """Current open-risk facts only; no proposed candidate sizing lives here."""

    observed_at: datetime
    tradable_at: datetime
    total_open_risk_pct: Decimal
    cluster_open_risk: tuple[RiskBucket, ...] = ()
    directional_open_risk: tuple[RiskBucket, ...] = ()
    venue_open_risk: tuple[RiskBucket, ...] = ()
    existing_exposures: tuple[ExistingPortfolioExposure, ...] = ()

    def __post_init__(self) -> None:
        _require_aware_datetime("observed_at", self.observed_at)
        _require_aware_datetime("tradable_at", self.tradable_at)
        if self.tradable_at < self.observed_at:
            raise ValueError("portfolio snapshot tradable_at must not precede observed_at")
        _require_non_negative_decimal("total_open_risk_pct", self.total_open_risk_pct)
        for collection, label in (
            (self.cluster_open_risk, "cluster_open_risk"),
            (self.directional_open_risk, "directional_open_risk"),
            (self.venue_open_risk, "venue_open_risk"),
        ):
            if any(not isinstance(item, RiskBucket) for item in collection):
                raise ValueError(f"{label} must contain RiskBucket values")
            keys = [item.key for item in collection]
            if len(keys) != len(set(keys)):
                raise ValueError(f"duplicate {label} key")
        if any(
            not isinstance(item, ExistingPortfolioExposure)
            for item in self.existing_exposures
        ):
            raise ValueError("existing_exposures must contain ExistingPortfolioExposure")
        exposure_keys = [item.exposure_key for item in self.existing_exposures]
        if len(exposure_keys) != len(set(exposure_keys)):
            raise ValueError("duplicate existing exposure key")


@dataclass(frozen=True, slots=True)
class PortfolioConflictPolicy:
    """Versioned admission caps supplied by configuration, not hidden sizing."""

    max_total_open_risk_pct: Decimal
    max_cluster_open_risk_pct: Decimal
    max_directional_open_risk_pct: Decimal
    max_venue_open_risk_pct: Decimal
    strong_risk_correlation_threshold: Decimal

    def __post_init__(self) -> None:
        for label in (
            "max_total_open_risk_pct",
            "max_cluster_open_risk_pct",
            "max_directional_open_risk_pct",
            "max_venue_open_risk_pct",
        ):
            value = getattr(self, label)
            _require_non_negative_decimal(label, value)
            if value == _ZERO:
                raise ValueError(f"{label} must be positive")
        _require_unit_decimal(
            "strong_risk_correlation_threshold",
            self.strong_risk_correlation_threshold,
        )
        if self.strong_risk_correlation_threshold == _ZERO:
            raise ValueError("strong_risk_correlation_threshold must be positive")


@dataclass(frozen=True, slots=True)
class PortfolioCandidateInput:
    candidate_key: str
    meta_score: EnsembleMetaScoreResult
    instrument_id: str
    underlying_key: str
    direction: Direction
    venue: str
    asset_class: AssetClass
    correlation_cluster: str | None = None

    def __post_init__(self) -> None:
        _require_text("candidate_key", self.candidate_key)
        if not isinstance(self.meta_score, EnsembleMetaScoreResult):
            raise ValueError("meta_score must be EnsembleMetaScoreResult")
        if self.meta_score.candidate_key != self.candidate_key:
            raise ValueError("candidate_key must match meta_score candidate_key")
        _require_text("instrument_id", self.instrument_id)
        _require_text("underlying_key", self.underlying_key)
        if not isinstance(self.direction, Direction):
            raise ValueError("direction must be Direction")
        _require_text("venue", self.venue)
        if not isinstance(self.asset_class, AssetClass):
            raise ValueError("asset_class must be AssetClass")
        if self.correlation_cluster is not None:
            _require_text("correlation_cluster", self.correlation_cluster)


@dataclass(frozen=True, slots=True)
class PairwiseCorrelationObservation:
    left_candidate_key: str
    right_candidate_key: str
    price_correlation: Decimal
    observed_at: datetime
    tradable_at: datetime
    source: str

    def __post_init__(self) -> None:
        _require_text("left_candidate_key", self.left_candidate_key)
        _require_text("right_candidate_key", self.right_candidate_key)
        if self.left_candidate_key == self.right_candidate_key:
            raise ValueError("correlation pair must contain distinct candidates")
        _require_finite_decimal("price_correlation", self.price_correlation)
        if not -_ONE <= self.price_correlation <= _ONE:
            raise ValueError("price_correlation must be between -1 and 1")
        _require_aware_datetime("observed_at", self.observed_at)
        _require_aware_datetime("tradable_at", self.tradable_at)
        if self.tradable_at < self.observed_at:
            raise ValueError("correlation tradable_at must not precede observed_at")
        _require_text("source", self.source)


@dataclass(frozen=True, slots=True)
class PortfolioCandidateResolution:
    policy_version: str
    candidate_key: str
    status: ResolutionStatus
    upstream_decision: RegimeGateDecision
    evidence_adjusted_edge_bps: Decimal
    evidence_weight: Decimal
    reasons: tuple[str, ...]
    conflicts_with: tuple[str, ...] = ()
    risk_correlation: Decimal | None = None

    def __post_init__(self) -> None:
        _require_text("policy_version", self.policy_version)
        _require_text("candidate_key", self.candidate_key)
        if not isinstance(self.status, ResolutionStatus):
            raise ValueError("status must be ResolutionStatus")
        if not isinstance(self.upstream_decision, RegimeGateDecision):
            raise ValueError("upstream_decision must be RegimeGateDecision")
        _require_finite_decimal(
            "evidence_adjusted_edge_bps", self.evidence_adjusted_edge_bps
        )
        _require_unit_decimal("evidence_weight", self.evidence_weight)
        if not self.reasons or any(not reason.strip() for reason in self.reasons):
            raise ValueError("reasons must contain non-blank reason codes")
        if any(not key.strip() for key in self.conflicts_with):
            raise ValueError("conflicts_with must contain non-blank identities")
        if self.risk_correlation is not None:
            _require_finite_decimal("risk_correlation", self.risk_correlation)
            if not -_ONE <= self.risk_correlation <= _ONE:
                raise ValueError("risk_correlation must be between -1 and 1")


def resolve_portfolio_conflicts(
    candidates: Sequence[PortfolioCandidateInput],
    *,
    snapshot: PortfolioExposureSnapshot,
    policy: PortfolioConflictPolicy,
    correlations: Sequence[PairwiseCorrelationObservation],
    evaluated_at: datetime,
) -> tuple[PortfolioCandidateResolution, ...]:
    """Resolve pre-approval candidate conflicts without calculating new risk size."""

    _require_aware_datetime("evaluated_at", evaluated_at)
    if not isinstance(snapshot, PortfolioExposureSnapshot):
        raise ValueError("snapshot must be PortfolioExposureSnapshot")
    if not isinstance(policy, PortfolioConflictPolicy):
        raise ValueError("policy must be PortfolioConflictPolicy")
    if snapshot.observed_at > evaluated_at or snapshot.tradable_at > evaluated_at:
        raise ValueError("portfolio snapshot is from the future or not yet tradable")

    candidate_list = list(candidates)
    if any(not isinstance(item, PortfolioCandidateInput) for item in candidate_list):
        raise ValueError("candidates must contain PortfolioCandidateInput")
    candidate_keys = [item.candidate_key for item in candidate_list]
    if len(candidate_keys) != len(set(candidate_keys)):
        raise ValueError("duplicate candidate identity")
    candidate_by_key = {item.candidate_key: item for item in candidate_list}

    correlation_map: dict[frozenset[str], PairwiseCorrelationObservation] = {}
    for observation in correlations:
        if not isinstance(observation, PairwiseCorrelationObservation):
            raise ValueError("correlations must contain PairwiseCorrelationObservation")
        if observation.observed_at > evaluated_at or observation.tradable_at > evaluated_at:
            raise ValueError("correlation evidence is from the future or not yet tradable")
        if (
            observation.left_candidate_key not in candidate_by_key
            or observation.right_candidate_key not in candidate_by_key
        ):
            raise ValueError("correlation evidence references unknown candidate")
        identity = frozenset(
            (observation.left_candidate_key, observation.right_candidate_key)
        )
        if identity in correlation_map:
            raise ValueError("duplicate correlation identity")
        correlation_map[identity] = observation

    cluster_risk = _bucket_map(snapshot.cluster_open_risk)
    directional_risk = _bucket_map(snapshot.directional_open_risk)
    venue_risk = _bucket_map(snapshot.venue_open_risk)

    ranked = sorted(candidate_list, key=_candidate_rank_key)
    selected: list[PortfolioCandidateInput] = []
    resolutions: list[PortfolioCandidateResolution] = []

    for candidate in ranked:
        meta = candidate.meta_score

        if meta.policy_version != _SUPPORTED_META_POLICY:
            resolutions.append(_blocked(candidate, "UNSUPPORTED_META_POLICY"))
            continue
        if meta.admission_decision is RegimeGateDecision.BLOCK:
            resolutions.append(_blocked(candidate, "UPSTREAM_ADMISSION_BLOCKED"))
            continue
        if snapshot.total_open_risk_pct >= policy.max_total_open_risk_pct:
            resolutions.append(_blocked(candidate, "TOTAL_RISK_CAPACITY_EXHAUSTED"))
            continue
        if (
            candidate.correlation_cluster is not None
            and cluster_risk.get(candidate.correlation_cluster, _ZERO)
            >= policy.max_cluster_open_risk_pct
        ):
            resolutions.append(_blocked(candidate, "CLUSTER_RISK_CAPACITY_EXHAUSTED"))
            continue
        if (
            directional_risk.get(candidate.direction.value, _ZERO)
            >= policy.max_directional_open_risk_pct
        ):
            resolutions.append(
                _blocked(candidate, "DIRECTIONAL_CONCENTRATION_CAPACITY_EXHAUSTED")
            )
            continue
        if venue_risk.get(candidate.venue, _ZERO) >= policy.max_venue_open_risk_pct:
            resolutions.append(_blocked(candidate, "VENUE_CONCENTRATION_CAPACITY_EXHAUSTED"))
            continue

        existing_matches = tuple(
            exposure.exposure_key
            for exposure in snapshot.existing_exposures
            if exposure.underlying_key == candidate.underlying_key
        )
        if existing_matches:
            resolutions.append(
                _blocked(
                    candidate,
                    "SAME_UNDERLYING_ALREADY_OPEN",
                    conflicts_with=existing_matches,
                )
            )
            continue

        same_underlying = next(
            (
                incumbent
                for incumbent in selected
                if incumbent.underlying_key == candidate.underlying_key
            ),
            None,
        )
        if same_underlying is not None:
            resolutions.append(
                _blocked(
                    candidate,
                    "SAME_UNDERLYING_CANDIDATE_CONFLICT",
                    conflicts_with=(same_underlying.candidate_key,),
                )
            )
            continue

        pair_conflict = _selected_pair_conflict(
            candidate,
            selected=selected,
            correlation_map=correlation_map,
            threshold=policy.strong_risk_correlation_threshold,
        )
        if pair_conflict is not None:
            reason, incumbent_key, risk_correlation = pair_conflict
            resolutions.append(
                _blocked(
                    candidate,
                    reason,
                    conflicts_with=(incumbent_key,),
                    risk_correlation=risk_correlation,
                )
            )
            continue

        selected.append(candidate)
        resolutions.append(
            PortfolioCandidateResolution(
                policy_version=_POLICY_VERSION,
                candidate_key=candidate.candidate_key,
                status=ResolutionStatus.SELECTED,
                upstream_decision=meta.admission_decision,
                evidence_adjusted_edge_bps=meta.evidence_adjusted_edge_bps,
                evidence_weight=meta.evidence_weight,
                reasons=("PORTFOLIO_CONFLICTS_CLEAR",),
            )
        )

    return tuple(resolutions)


def _selected_pair_conflict(
    candidate: PortfolioCandidateInput,
    *,
    selected: Sequence[PortfolioCandidateInput],
    correlation_map: dict[frozenset[str], PairwiseCorrelationObservation],
    threshold: Decimal,
) -> tuple[str, str, Decimal | None] | None:
    for incumbent in selected:
        identity = frozenset((candidate.candidate_key, incumbent.candidate_key))
        observation = correlation_map.get(identity)
        if observation is None:
            if candidate.asset_class is not incumbent.asset_class:
                return (
                    "CROSS_ASSET_CORRELATION_MISSING",
                    incumbent.candidate_key,
                    None,
                )
            continue

        risk_correlation = observation.price_correlation * _direction_sign(
            candidate.direction
        ) * _direction_sign(incumbent.direction)
        if risk_correlation >= threshold:
            return (
                "STRONG_RISK_CORRELATION_CONFLICT",
                incumbent.candidate_key,
                risk_correlation,
            )
    return None


def _candidate_rank_key(candidate: PortfolioCandidateInput) -> tuple[object, ...]:
    decision_rank = {
        RegimeGateDecision.ALLOW: 0,
        RegimeGateDecision.REDUCE: 1,
        RegimeGateDecision.BLOCK: 2,
    }
    meta = candidate.meta_score
    return (
        decision_rank[meta.admission_decision],
        -meta.evidence_adjusted_edge_bps,
        -meta.evidence_weight,
        candidate.candidate_key,
    )


def _direction_sign(direction: Direction) -> Decimal:
    return _ONE if direction is Direction.LONG else Decimal("-1")


def _bucket_map(buckets: Sequence[RiskBucket]) -> dict[str, Decimal]:
    return {item.key: item.open_risk_pct for item in buckets}


def _blocked(
    candidate: PortfolioCandidateInput,
    reason: str,
    *,
    conflicts_with: tuple[str, ...] = (),
    risk_correlation: Decimal | None = None,
) -> PortfolioCandidateResolution:
    meta = candidate.meta_score
    return PortfolioCandidateResolution(
        policy_version=_POLICY_VERSION,
        candidate_key=candidate.candidate_key,
        status=ResolutionStatus.BLOCKED,
        upstream_decision=meta.admission_decision,
        evidence_adjusted_edge_bps=meta.evidence_adjusted_edge_bps,
        evidence_weight=meta.evidence_weight,
        reasons=(reason,),
        conflicts_with=conflicts_with,
        risk_correlation=risk_correlation,
    )


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")


def _require_finite_decimal(label: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{label} must be a finite Decimal")


def _require_non_negative_decimal(label: str, value: Decimal) -> None:
    _require_finite_decimal(label, value)
    if value < _ZERO:
        raise ValueError(f"{label} must be non-negative")


def _require_unit_decimal(label: str, value: Decimal) -> None:
    _require_finite_decimal(label, value)
    if not _ZERO <= value <= _ONE:
        raise ValueError(f"{label} must be between 0 and 1")


def _require_aware_datetime(label: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


__all__ = [
    "AssetClass",
    "ExistingPortfolioExposure",
    "PairwiseCorrelationObservation",
    "PortfolioCandidateInput",
    "PortfolioCandidateResolution",
    "PortfolioConflictPolicy",
    "PortfolioExposureSnapshot",
    "ResolutionStatus",
    "RiskBucket",
    "resolve_portfolio_conflicts",
]
