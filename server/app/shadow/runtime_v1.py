"""Pure Shadow observation runtime for R4 candidate strategies.

Shadow is deliberately outside ``TradeIdea`` and every execution lifecycle.
It turns point-in-time ``StrategyResultV2`` outputs (or explicit absence of an
output) into deterministic measurement observations.  The result contains no
position size, leverage, stop/target, order, notification, approval or paper
trade semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256

from ..models.enums import Direction
from ..strategies.result_v2 import DataQualityState, StrategyResultV2

_STAGE = "SHADOW"


@dataclass(frozen=True, slots=True)
class ShadowEvaluationInput:
    instrument_id: str
    venue: str
    market_snapshot_hash: str
    cost_model_hash: str
    evaluated_at: datetime
    candidates: tuple[StrategyResultV2, ...]
    candidate_versions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text("instrument_id", self.instrument_id)
        _require_text("venue", self.venue)
        _require_sha256("market_snapshot_hash", self.market_snapshot_hash)
        _require_sha256("cost_model_hash", self.cost_model_hash)
        _require_aware_datetime("evaluated_at", self.evaluated_at)
        if any(not isinstance(item, StrategyResultV2) for item in self.candidates):
            raise ValueError("candidates must contain StrategyResultV2 values")
        if any(not isinstance(item, str) or not item.strip() for item in self.candidate_versions):
            raise ValueError("candidate_versions must contain non-blank strings")
        if len(self.candidate_versions) != len(set(self.candidate_versions)):
            raise ValueError("duplicate strategy version in candidate manifest")


@dataclass(frozen=True, slots=True)
class ShadowCandidateObservation:
    observation_key: str
    stage: str
    instrument_id: str
    venue: str
    strategy_family: str | None
    strategy_version: str
    signal_emitted: bool
    direction: Direction | None
    raw_edge_score: Decimal | None
    entry_reference: Decimal | None
    data_quality_state: DataQualityState | None
    evaluated_at: datetime
    market_snapshot_hash: str
    cost_model_hash: str

    def __post_init__(self) -> None:
        _require_sha256("observation_key", self.observation_key)
        if self.stage != _STAGE:
            raise ValueError("Shadow observation stage must be SHADOW")
        _require_text("instrument_id", self.instrument_id)
        _require_text("venue", self.venue)
        _require_text("strategy_version", self.strategy_version)
        _require_aware_datetime("evaluated_at", self.evaluated_at)
        _require_sha256("market_snapshot_hash", self.market_snapshot_hash)
        _require_sha256("cost_model_hash", self.cost_model_hash)
        if self.strategy_family is not None:
            _require_text("strategy_family", self.strategy_family)
        if not isinstance(self.signal_emitted, bool):
            raise ValueError("signal_emitted must be bool")
        if self.signal_emitted:
            if not isinstance(self.direction, Direction):
                raise ValueError("emitted Shadow observation requires direction")
            if not isinstance(self.raw_edge_score, Decimal) or not self.raw_edge_score.is_finite():
                raise ValueError("emitted Shadow observation requires finite raw_edge_score")
            if not isinstance(self.entry_reference, Decimal) or not self.entry_reference.is_finite():
                raise ValueError("emitted Shadow observation requires finite entry_reference")
            if not isinstance(self.data_quality_state, DataQualityState):
                raise ValueError("emitted Shadow observation requires data_quality_state")
        elif any(
            value is not None
            for value in (
                self.direction,
                self.raw_edge_score,
                self.entry_reference,
                self.data_quality_state,
            )
        ):
            raise ValueError("no-signal Shadow observation must not invent candidate values")


def evaluate_shadow_candidates(
    payload: ShadowEvaluationInput,
) -> tuple[ShadowCandidateObservation, ...]:
    """Create deterministic Shadow observations from one point-in-time snapshot."""

    if not isinstance(payload, ShadowEvaluationInput):
        raise ValueError("payload must be ShadowEvaluationInput")

    candidates_by_version: dict[str, StrategyResultV2] = {}
    for candidate in payload.candidates:
        if candidate.strategy_version in candidates_by_version:
            raise ValueError("duplicate strategy version in candidate output")
        if candidate.evaluated_at > payload.evaluated_at:
            raise ValueError("candidate evidence is from the future")
        if any(
            feature.observed_at > payload.evaluated_at
            or feature.tradable_at > payload.evaluated_at
            for feature in candidate.feature_provenance
        ):
            raise ValueError("candidate feature evidence is from the future")
        candidates_by_version[candidate.strategy_version] = candidate

    manifest = payload.candidate_versions or tuple(candidates_by_version)
    if len(manifest) != len(set(manifest)):
        raise ValueError("duplicate strategy version in candidate manifest")
    undeclared = set(candidates_by_version) - set(manifest)
    if undeclared:
        raise ValueError("candidate output is not present in candidate manifest")

    observations: list[ShadowCandidateObservation] = []
    for version in manifest:
        candidate = candidates_by_version.get(version)
        key = _observation_key(
            instrument_id=payload.instrument_id,
            venue=payload.venue,
            strategy_version=version,
            market_snapshot_hash=payload.market_snapshot_hash,
            cost_model_hash=payload.cost_model_hash,
            evaluated_at=payload.evaluated_at,
        )
        if candidate is None:
            observations.append(
                ShadowCandidateObservation(
                    observation_key=key,
                    stage=_STAGE,
                    instrument_id=payload.instrument_id,
                    venue=payload.venue,
                    strategy_family=None,
                    strategy_version=version,
                    signal_emitted=False,
                    direction=None,
                    raw_edge_score=None,
                    entry_reference=None,
                    data_quality_state=None,
                    evaluated_at=payload.evaluated_at,
                    market_snapshot_hash=payload.market_snapshot_hash,
                    cost_model_hash=payload.cost_model_hash,
                )
            )
            continue

        observations.append(
            ShadowCandidateObservation(
                observation_key=key,
                stage=_STAGE,
                instrument_id=payload.instrument_id,
                venue=payload.venue,
                strategy_family=candidate.strategy_family,
                strategy_version=candidate.strategy_version,
                signal_emitted=True,
                direction=candidate.direction,
                raw_edge_score=candidate.raw_edge_score,
                entry_reference=candidate.entry_hypothesis.reference,
                data_quality_state=candidate.data_quality_state,
                evaluated_at=payload.evaluated_at,
                market_snapshot_hash=payload.market_snapshot_hash,
                cost_model_hash=payload.cost_model_hash,
            )
        )
    return tuple(observations)


def _observation_key(
    *,
    instrument_id: str,
    venue: str,
    strategy_version: str,
    market_snapshot_hash: str,
    cost_model_hash: str,
    evaluated_at: datetime,
) -> str:
    identity = "|".join(
        (
            _STAGE,
            instrument_id,
            venue,
            strategy_version,
            market_snapshot_hash,
            cost_model_hash,
            evaluated_at.isoformat(),
        )
    )
    return sha256(identity.encode("utf-8")).hexdigest()


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")


def _require_sha256(label: str, value: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{label} must be a 64-character SHA-256 identity")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{label} must be a 64-character SHA-256 identity") from exc


def _require_aware_datetime(label: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


__all__ = [
    "ShadowCandidateObservation",
    "ShadowEvaluationInput",
    "evaluate_shadow_candidates",
]
