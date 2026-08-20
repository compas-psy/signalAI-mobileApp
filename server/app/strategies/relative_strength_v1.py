"""Cross-sectional relative-strength ranking boost for R4 candidate strategies.

The ranker deliberately emits no trade plan and makes no admission decision.  It
normalizes already-comparable momentum evidence across the currently tradable
universe, combines it with quality, and applies only a soft same-risk cluster
crowding penalty.  Hard portfolio conflict resolution remains downstream.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from ..models.enums import Direction

MOMENTUM_WEIGHT = Decimal("0.65")
QUALITY_WEIGHT = Decimal("0.25")
DIVERSIFICATION_WEIGHT = Decimal("0.10")


@dataclass(frozen=True, slots=True)
class RelativeStrengthObservation:
    instrument_id: str
    direction: Direction
    normalized_momentum: Decimal
    quality_score: Decimal
    correlation_cluster: str | None
    observed_at: datetime
    tradable_at: datetime
    source: str

    def __post_init__(self) -> None:
        _require_text("instrument_id", self.instrument_id)
        if not isinstance(self.direction, Direction):
            raise ValueError("direction must be Direction")
        _require_finite_decimal("normalized_momentum", self.normalized_momentum)
        _require_finite_decimal("quality_score", self.quality_score)
        if not Decimal(0) <= self.quality_score <= Decimal(1):
            raise ValueError("quality_score must be between 0 and 1")
        if self.correlation_cluster is not None and not self.correlation_cluster.strip():
            raise ValueError("correlation_cluster must be non-blank when provided")
        _require_aware_datetime("observed_at", self.observed_at)
        _require_aware_datetime("tradable_at", self.tradable_at)
        if self.tradable_at < self.observed_at:
            raise ValueError("tradable_at must not precede observed_at")
        _require_text("source", self.source)

    @property
    def directional_momentum(self) -> Decimal:
        return (
            self.normalized_momentum
            if self.direction is Direction.LONG
            else -self.normalized_momentum
        )


@dataclass(frozen=True, slots=True)
class RelativeStrengthRank:
    instrument_id: str
    direction: Direction
    directional_momentum: Decimal
    momentum_percentile: Decimal
    quality_score: Decimal
    crowding_factor: Decimal
    ranking_boost: Decimal
    correlation_cluster: str | None
    observed_at: datetime
    tradable_at: datetime
    source: str
    explanation: str


def rank_relative_strength(
    observations: Sequence[RelativeStrengthObservation],
    *,
    evaluated_at: datetime,
) -> tuple[RelativeStrengthRank, ...]:
    """Return deterministic relative-strength boosts for the visible universe."""

    _require_aware_datetime("evaluated_at", evaluated_at)
    visible = [
        item
        for item in observations
        if item.observed_at <= evaluated_at and item.tradable_at <= evaluated_at
    ]
    if not visible:
        return ()

    keys = [(item.instrument_id, item.direction) for item in visible]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate instrument+direction observation")

    percentiles = _midrank_percentiles(
        [item.directional_momentum for item in visible]
    )
    cluster_counts = Counter(
        (item.correlation_cluster, item.direction)
        for item in visible
        if item.correlation_cluster is not None
    )

    ranked: list[RelativeStrengthRank] = []
    for item, momentum_percentile in zip(visible, percentiles, strict=True):
        if item.correlation_cluster is None:
            crowding = Decimal(1)
        else:
            count = cluster_counts[(item.correlation_cluster, item.direction)]
            crowding = Decimal(1) / Decimal(count)

        boost = (
            MOMENTUM_WEIGHT * momentum_percentile
            + QUALITY_WEIGHT * item.quality_score
            + DIVERSIFICATION_WEIGHT * crowding
        ).quantize(Decimal("0.000001"))
        ranked.append(
            RelativeStrengthRank(
                instrument_id=item.instrument_id,
                direction=item.direction,
                directional_momentum=item.directional_momentum,
                momentum_percentile=momentum_percentile,
                quality_score=item.quality_score,
                crowding_factor=crowding,
                ranking_boost=boost,
                correlation_cluster=item.correlation_cluster,
                observed_at=item.observed_at,
                tradable_at=item.tradable_at,
                source=item.source,
                explanation=(
                    f"momentum percentile {momentum_percentile}; quality "
                    f"{item.quality_score}; same-risk crowding factor {crowding}"
                ),
            )
        )

    return tuple(
        sorted(
            ranked,
            key=lambda item: (-item.ranking_boost, item.instrument_id, item.direction.value),
        )
    )


def _midrank_percentiles(values: Sequence[Decimal]) -> list[Decimal]:
    """Percentile rank with deterministic midranks; a one-item universe is neutral."""

    if len(values) == 1:
        return [Decimal("0.5")]

    positions: dict[Decimal, list[int]] = defaultdict(list)
    for position, value in enumerate(sorted(values)):
        positions[value].append(position)

    denominator = Decimal(len(values) - 1)
    percentile_by_value: dict[Decimal, Decimal] = {}
    for value, value_positions in positions.items():
        mid_position = sum(Decimal(position) for position in value_positions) / Decimal(
            len(value_positions)
        )
        percentile_by_value[value] = mid_position / denominator
    return [percentile_by_value[value] for value in values]


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")


def _require_finite_decimal(label: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{label} must be a finite Decimal")


def _require_aware_datetime(label: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


__all__ = [
    "RelativeStrengthObservation",
    "RelativeStrengthRank",
    "rank_relative_strength",
]
