"""R4 crypto funding/carry/basis challenger.

The strategy consumes immutable public market facts and pre-resolved cost
assumptions.  It performs no HTTP requests, owns no venue credentials and
creates no hedge/order intent.  Its only output is candidate evidence for the
later admission/OOS layers.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from ..market.derivatives import CryptoCarryMarketFacts, FundingObservation
from ..models.enums import Direction
from .result_v2 import (
    DataQualityState,
    EntryHypothesis,
    ExplanationComponent,
    FeatureProvenance,
    StrategyHorizon,
    StrategyResultV2,
)

_BPS = Decimal("10000")
MIN_FUNDING_OBSERVATIONS = 6
MIN_SAME_SIGN_RATIO = Decimal("0.75")
TARGET_FUNDING_INTERVALS = 3
MAX_LAST_SETTLEMENT_AGE_INTERVALS = 2
MIN_NET_CARRY_BPS = Decimal("1")


def evaluate_crypto_carry_v1(
    *,
    facts: CryptoCarryMarketFacts,
    execution_cost_bps: Decimal,
    hedge_carry_bps_per_interval: Decimal,
    funding_uncertainty_bps_per_interval: Decimal,
    evaluated_at: datetime,
) -> StrategyResultV2 | None:
    """Evaluate persistent hedged funding carry after conservative costs."""

    _require_aware_datetime("evaluated_at", evaluated_at)
    for name, value in (
        ("execution_cost_bps", execution_cost_bps),
        ("hedge_carry_bps_per_interval", hedge_carry_bps_per_interval),
        ("funding_uncertainty_bps_per_interval", funding_uncertainty_bps_per_interval),
    ):
        _require_non_negative_decimal(name, value)

    if facts.tradable_at > evaluated_at:
        return None

    history = tuple(
        sorted(
            (
                item
                for item in facts.funding_history
                if item.tradable_at <= evaluated_at
            ),
            key=lambda item: item.settled_at,
        )
    )
    if len(history) < MIN_FUNDING_OBSERVATIONS:
        return None

    interval = timedelta(minutes=facts.funding_interval_minutes)
    latest = history[-1]
    if evaluated_at - latest.tradable_at > interval * MAX_LAST_SETTLEMENT_AGE_INTERVALS:
        return None

    median_funding = _median(tuple(item.rate for item in history))
    if median_funding == 0:
        return None

    direction = Direction.SHORT if median_funding > 0 else Direction.LONG
    sign = Decimal(1) if median_funding > 0 else Decimal(-1)
    same_sign_count = sum(1 for item in history if item.rate * sign > 0)
    same_sign_ratio = Decimal(same_sign_count) / Decimal(len(history))
    if same_sign_ratio < MIN_SAME_SIGN_RATIO:
        return None
    if facts.current_funding_rate * sign <= 0:
        return None

    projected_funding_bps = (
        abs(median_funding) * _BPS * Decimal(TARGET_FUNDING_INTERVALS)
    )
    hedge_carry_bps = hedge_carry_bps_per_interval * Decimal(TARGET_FUNDING_INTERVALS)
    funding_uncertainty_bps = (
        funding_uncertainty_bps_per_interval * Decimal(TARGET_FUNDING_INTERVALS)
    )
    basis_rate = facts.mark_index_basis_rate
    # Full absolute mark/index dislocation is treated as a haircut, not alpha.
    # This is deliberately conservative until OOS proves a better convergence model.
    basis_convergence_risk_bps = abs(basis_rate) * _BPS
    net_carry_bps = (
        projected_funding_bps
        - execution_cost_bps
        - hedge_carry_bps
        - funding_uncertainty_bps
        - basis_convergence_risk_bps
    )
    if net_carry_bps < MIN_NET_CARRY_BPS:
        return None

    raw_edge_score = min(net_carry_bps / Decimal("20"), Decimal(1)).quantize(
        Decimal("0.0001")
    )
    observed_at = max(facts.observed_at, latest.tradable_at)
    provenance = (
        _feature("median_funding_rate", median_funding, "funding_history", observed_at, evaluated_at),
        _feature("funding_same_sign_ratio", same_sign_ratio, "funding_history", observed_at, evaluated_at),
        _feature("mark_index_basis_rate", basis_rate, facts.source, facts.observed_at, facts.tradable_at),
        _feature("projected_funding_bps", projected_funding_bps, "carry_projection", observed_at, evaluated_at),
        _feature("execution_cost_bps", execution_cost_bps, "canonical_cost_model", observed_at, evaluated_at),
        _feature("hedge_carry_bps", hedge_carry_bps, "hedge_cost_assumption", observed_at, evaluated_at),
        _feature("funding_uncertainty_bps", funding_uncertainty_bps, "funding_uncertainty_haircut", observed_at, evaluated_at),
        _feature("basis_convergence_risk_bps", basis_convergence_risk_bps, "mark_index_basis_haircut", observed_at, evaluated_at),
        _feature("net_carry_bps", net_carry_bps, "carry_net_after_costs", observed_at, evaluated_at),
        _feature("entry_reference", facts.mark_price, facts.source, facts.observed_at, facts.tradable_at),
    )
    funding_strength = min(
        projected_funding_bps / Decimal("30"), Decimal(1)
    ) * Decimal("0.45")
    persistence_strength = min(same_sign_ratio, Decimal(1)) * Decimal("0.30")
    cost_survival = min(net_carry_bps / projected_funding_bps, Decimal(1)) * Decimal("0.25")
    explanations = (
        ExplanationComponent(
            name="persistent_funding",
            contribution=funding_strength,
            detail="median funding is persistent across multiple settled prints",
        ),
        ExplanationComponent(
            name="same_sign_history",
            contribution=persistence_strength,
            detail="funding direction persists across the observation window",
        ),
        ExplanationComponent(
            name="net_after_costs",
            contribution=cost_survival,
            detail="projected carry survives execution, hedge, uncertainty and basis haircuts",
        ),
    )
    leg = "short" if direction is Direction.SHORT else "long"
    funding_sign = "positive" if median_funding > 0 else "negative"

    return StrategyResultV2(
        strategy_family="CRYPTO_CARRY",
        strategy_version="crypto_carry_v1",
        direction=direction,
        raw_edge_score=raw_edge_score,
        entry_hypothesis=EntryHypothesis(
            kind="HEDGED_CARRY",
            reference=facts.mark_price,
            rationale=(
                f"{leg} perpetual leg against a hedge after persistent {funding_sign} "
                "funding survives conservative all-in costs"
            ),
        ),
        invalidation=(
            "Carry invalidates if funding persistence/sign breaks or refreshed execution, "
            "hedge, uncertainty or basis-convergence costs remove net edge"
        ),
        horizon=StrategyHorizon(
            value=facts.funding_interval_minutes * TARGET_FUNDING_INTERVALS,
            unit="MINUTES",
        ),
        feature_provenance=provenance,
        regime_compatibility=("CARRY", "FUNDING_PERSISTENT"),
        data_quality_state=DataQualityState.GOOD,
        explanation_components=explanations,
        evaluated_at=evaluated_at,
    )


def _median(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("median sample must not be empty")
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


def _feature(
    name: str,
    value: Decimal,
    source: str,
    observed_at: datetime,
    tradable_at: datetime,
) -> FeatureProvenance:
    return FeatureProvenance(
        name=name,
        value=str(value),
        source=source,
        observed_at=observed_at,
        tradable_at=tradable_at,
    )


def _require_non_negative_decimal(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite non-negative Decimal")


def _require_aware_datetime(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = ["evaluate_crypto_carry_v1"]
