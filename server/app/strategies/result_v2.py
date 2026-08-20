"""Pure candidate-strategy output contract for the R4 signal stack.

``StrategyResultV2`` deliberately stops before risk and execution.  A strategy
may describe an edge and an entry *hypothesis*, but position sizing, leverage,
order intent, protective orders and venue execution belong to downstream
layers.  Keeping this contract beside (rather than replacing) ``base.Candidate``
preserves the frozen ``legacy_control_v1`` behavior while new challengers are
built and measured in parallel.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from ..models.enums import Direction


class DataQualityState(StrEnum):
    """Whether the strategy had enough trustworthy data to form evidence."""

    GOOD = "GOOD"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class EntryHypothesis:
    """Price hypothesis only; this is intentionally not an order request."""

    kind: str
    reference: Decimal
    lower: Decimal | None = None
    upper: Decimal | None = None
    rationale: str = ""

    def __post_init__(self) -> None:
        _require_text("entry hypothesis kind", self.kind)
        _require_finite_decimal("entry hypothesis reference", self.reference)
        _require_text("entry hypothesis rationale", self.rationale)

        if self.lower is not None:
            _require_finite_decimal("entry hypothesis lower", self.lower)
        if self.upper is not None:
            _require_finite_decimal("entry hypothesis upper", self.upper)
        if (self.lower is None) != (self.upper is None):
            raise ValueError("entry hypothesis zone requires both lower and upper")
        if self.lower is not None and self.upper is not None:
            if self.lower > self.upper:
                raise ValueError("entry hypothesis lower must not exceed upper")
            if not self.lower <= self.reference <= self.upper:
                raise ValueError("entry hypothesis reference must be inside zone")


@dataclass(frozen=True, slots=True)
class StrategyHorizon:
    """Explicit horizon without coupling a strategy to an execution timeout."""

    value: int
    unit: str

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or self.value <= 0:
            raise ValueError("horizon value must be a positive integer")
        _require_text("horizon unit", self.unit)


@dataclass(frozen=True, slots=True)
class FeatureProvenance:
    """One feature value plus the point in time when it became usable.

    ``tradable_at`` is the anti-look-ahead boundary used by OOS/backtest code.
    Values stay textual here so provenance can preserve exact Decimal values or
    categorical states without a lossy float conversion.
    """

    name: str
    value: str
    source: str
    observed_at: datetime
    tradable_at: datetime

    def __post_init__(self) -> None:
        _require_text("feature name", self.name)
        _require_text("feature value", self.value)
        _require_text("feature source", self.source)
        _require_aware_datetime("feature observed_at", self.observed_at)
        _require_aware_datetime("feature tradable_at", self.tradable_at)


@dataclass(frozen=True, slots=True)
class ExplanationComponent:
    """Machine-readable contribution plus owner-facing deterministic detail."""

    name: str
    contribution: Decimal
    detail: str

    def __post_init__(self) -> None:
        _require_text("explanation component name", self.name)
        _require_finite_decimal("explanation contribution", self.contribution)
        _require_text("explanation component detail", self.detail)


@dataclass(frozen=True, slots=True)
class StrategyResultV2:
    """Candidate signal evidence before admission, risk and execution.

    The string family/version identity intentionally does not extend the legacy
    ``Strategy`` enum: R4 challengers must coexist with the frozen CONTROL and
    may be registered/versioned independently before promotion.
    """

    strategy_family: str
    strategy_version: str
    direction: Direction
    raw_edge_score: Decimal
    entry_hypothesis: EntryHypothesis
    invalidation: str
    horizon: StrategyHorizon
    feature_provenance: tuple[FeatureProvenance, ...]
    regime_compatibility: tuple[str, ...]
    data_quality_state: DataQualityState
    explanation_components: tuple[ExplanationComponent, ...]
    evaluated_at: datetime

    def __post_init__(self) -> None:
        _require_text("strategy_family", self.strategy_family)
        _require_text("strategy_version", self.strategy_version)
        if not isinstance(self.direction, Direction):
            raise ValueError("direction must be a Direction")
        _require_finite_decimal("raw_edge_score", self.raw_edge_score)
        _require_text("invalidation", self.invalidation)
        _require_aware_datetime("evaluated_at", self.evaluated_at)

        if not self.feature_provenance:
            raise ValueError("feature_provenance must not be empty")
        if not self.regime_compatibility:
            raise ValueError("regime_compatibility must not be empty")
        for regime in self.regime_compatibility:
            _require_text("regime compatibility", regime)
        if not isinstance(self.data_quality_state, DataQualityState):
            raise ValueError("data_quality_state must be a DataQualityState")
        if not self.explanation_components:
            raise ValueError("explanation_components must not be empty")

        # A feature that only becomes tradable after evaluation is look-ahead.
        for feature in self.feature_provenance:
            if feature.tradable_at > self.evaluated_at:
                raise ValueError(
                    f"feature {feature.name!r} tradable_at is after evaluated_at"
                )


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")


def _require_finite_decimal(label: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{label} must be a finite Decimal")


def _require_aware_datetime(label: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
