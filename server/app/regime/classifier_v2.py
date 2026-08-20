"""Probabilistic regime classifier for the R4 candidate signal stack.

The legacy discrete classifier remains untouched.  This v2 layer consumes
already-deterministic normalized features and exposes orthogonal probability /
score axes rather than collapsing market state into one magical label.  The
versioned Strategy×Regime allow/reduce/block decision belongs to SAI-059.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class RegimeFeatureVector:
    """Point-in-time normalized deterministic regime features.

    Every numeric feature is dimensionless on [0, 1].  Normalization remains a
    data/feature responsibility so the classifier is deterministic and does not
    smuggle venue-specific absolute volatility or spread thresholds into regime
    semantics.
    """

    trend_strength: Decimal
    realized_vol_score: Decimal
    vol_of_vol_score: Decimal
    chop_score: Decimal
    liquidity_spread_stress: Decimal
    stress_score: Decimal
    observed_at: datetime
    tradable_at: datetime
    source: str

    def __post_init__(self) -> None:
        for name in (
            "trend_strength",
            "realized_vol_score",
            "vol_of_vol_score",
            "chop_score",
            "liquidity_spread_stress",
            "stress_score",
        ):
            _require_unit_interval(name, getattr(self, name))
        _require_aware_datetime("observed_at", self.observed_at)
        _require_aware_datetime("tradable_at", self.tradable_at)
        if self.tradable_at < self.observed_at:
            raise ValueError("tradable_at must not precede observed_at")
        _require_text("source", self.source)


@dataclass(frozen=True, slots=True)
class RegimeFeatureProvenance:
    name: str
    value: Decimal
    source: str
    observed_at: datetime
    tradable_at: datetime


@dataclass(frozen=True, slots=True)
class RegimeClassificationV2:
    version: str
    trend_probability: Decimal
    range_probability: Decimal
    low_vol_probability: Decimal
    normal_vol_probability: Decimal
    high_vol_probability: Decimal
    liquidity_stress_score: Decimal
    stress_probability: Decimal
    feature_provenance: tuple[RegimeFeatureProvenance, ...]
    evaluated_at: datetime

    def __post_init__(self) -> None:
        if self.trend_probability + self.range_probability != _ONE:
            raise ValueError("structure probabilities must sum to 1")
        if (
            self.low_vol_probability
            + self.normal_vol_probability
            + self.high_vol_probability
            != _ONE
        ):
            raise ValueError("volatility probabilities must sum to 1")
        for name in (
            "trend_probability",
            "range_probability",
            "low_vol_probability",
            "normal_vol_probability",
            "high_vol_probability",
            "liquidity_stress_score",
            "stress_probability",
        ):
            _require_unit_interval(name, getattr(self, name))
        _require_aware_datetime("evaluated_at", self.evaluated_at)


def classify_regime_v2(
    features: RegimeFeatureVector,
    *,
    evaluated_at: datetime,
) -> RegimeClassificationV2 | None:
    """Map visible deterministic features to orthogonal regime probabilities."""

    if not isinstance(features, RegimeFeatureVector):
        raise ValueError("features must be RegimeFeatureVector")
    _require_aware_datetime("evaluated_at", evaluated_at)
    if features.observed_at > evaluated_at or features.tradable_at > evaluated_at:
        return None

    trend_raw = (
        Decimal("0.10")
        + features.trend_strength * (_ONE - features.chop_score)
        + Decimal("0.25") * features.trend_strength
    )
    range_raw = (
        Decimal("0.10")
        + features.chop_score * (_ONE - features.trend_strength)
        + Decimal("0.25") * features.chop_score
    )
    trend_probability, range_probability = _normalize_two(trend_raw, range_raw)

    realized = features.realized_vol_score
    vol_of_vol = features.vol_of_vol_score
    low_raw = (
        Decimal("0.05")
        + (_ONE - realized) ** 2
        * (_ONE - Decimal("0.25") * vol_of_vol)
    )
    normal_shape = _ONE - abs(Decimal("2") * realized - _ONE)
    normal_raw = (
        Decimal("0.05")
        + normal_shape * (_ONE - Decimal("0.35") * vol_of_vol)
    )
    high_raw = (
        Decimal("0.05")
        + realized**2 * (Decimal("0.65") + Decimal("0.35") * vol_of_vol)
        + Decimal("0.45") * vol_of_vol
    )
    low_probability, normal_probability, high_probability = _normalize_three(
        low_raw, normal_raw, high_raw
    )

    # Stress is independent of the trend/range and volatility distributions.
    # A liquid high-volatility trend need not be a market-stress event, while
    # poor liquidity/spread or an explicit stress detector must raise caution.
    stress_probability = _clamp_unit(
        Decimal("0.55") * features.stress_score
        + Decimal("0.30") * features.liquidity_spread_stress
        + Decimal("0.15") * features.vol_of_vol_score
    )

    provenance = tuple(
        RegimeFeatureProvenance(
            name=name,
            value=getattr(features, name),
            source=features.source,
            observed_at=features.observed_at,
            tradable_at=features.tradable_at,
        )
        for name in (
            "trend_strength",
            "realized_vol_score",
            "vol_of_vol_score",
            "chop_score",
            "liquidity_spread_stress",
            "stress_score",
        )
    )

    return RegimeClassificationV2(
        version="regime_classifier_v2",
        trend_probability=trend_probability,
        range_probability=range_probability,
        low_vol_probability=low_probability,
        normal_vol_probability=normal_probability,
        high_vol_probability=high_probability,
        liquidity_stress_score=features.liquidity_spread_stress,
        stress_probability=stress_probability,
        feature_provenance=provenance,
        evaluated_at=evaluated_at,
    )


def _normalize_two(first: Decimal, second: Decimal) -> tuple[Decimal, Decimal]:
    total = first + second
    if total <= 0:
        return Decimal("0.5"), Decimal("0.5")
    first_probability = first / total
    return first_probability, _ONE - first_probability


def _normalize_three(
    first: Decimal,
    second: Decimal,
    third: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    total = first + second + third
    if total <= 0:
        one_third = _ONE / Decimal("3")
        return one_third, one_third, _ONE - one_third - one_third
    first_probability = first / total
    second_probability = second / total
    return first_probability, second_probability, _ONE - first_probability - second_probability


def _clamp_unit(value: Decimal) -> Decimal:
    return min(_ONE, max(_ZERO, value))


def _require_unit_interval(label: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or not _ZERO <= value <= _ONE:
        raise ValueError(f"{label} must be a finite Decimal between 0 and 1")


def _require_aware_datetime(label: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")


__all__ = [
    "RegimeClassificationV2",
    "RegimeFeatureProvenance",
    "RegimeFeatureVector",
    "classify_regime_v2",
]
