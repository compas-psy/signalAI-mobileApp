"""Versioned Strategy×Regime admission evidence for the R4 candidate stack.

This layer consumes a ``StrategyResultV2`` and the SAI-058 probabilistic regime
classification at the same point in time.  It produces only deterministic
ALLOW / REDUCE / BLOCK evidence.  It deliberately owns no position sizing,
risk budget, stop/target construction, order intent or venue execution.

The policy table is authoritative and keyed by the exact strategy family and
version.  A candidate's self-declared ``regime_compatibility`` is explanatory
metadata only and cannot widen the policy used for admission.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from ..strategies.result_v2 import DataQualityState, StrategyResultV2
from .classifier_v2 import RegimeClassificationV2

_ZERO = Decimal("0")
_ONE = Decimal("1")
_ONE_THIRD = _ONE / Decimal("3")
_TWO_THIRDS = Decimal("2") / Decimal("3")
_POLICY_VERSION = "strategy_regime_gate_v1"
_CLASSIFIER_VERSION = "regime_classifier_v2"


class RegimeGateDecision(StrEnum):
    ALLOW = "ALLOW"
    REDUCE = "REDUCE"
    BLOCK = "BLOCK"


@dataclass(frozen=True, slots=True)
class StrategyRegimeGateResult:
    """Pure admission evidence; never an execution or risk instruction."""

    policy_version: str
    classifier_version: str
    strategy_family: str
    strategy_version: str
    decision: RegimeGateDecision
    compatibility_score: Decimal
    structure_fit: Decimal | None
    volatility_fit: Decimal | None
    stress_fit: Decimal
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.policy_version.strip():
            raise ValueError("policy_version must not be blank")
        if not self.classifier_version.strip():
            raise ValueError("classifier_version must not be blank")
        if not self.strategy_family.strip():
            raise ValueError("strategy_family must not be blank")
        if not self.strategy_version.strip():
            raise ValueError("strategy_version must not be blank")
        if not isinstance(self.decision, RegimeGateDecision):
            raise ValueError("decision must be RegimeGateDecision")
        _require_unit("compatibility_score", self.compatibility_score)
        if self.structure_fit is not None:
            _require_unit("structure_fit", self.structure_fit)
        if self.volatility_fit is not None:
            _require_unit("volatility_fit", self.volatility_fit)
        _require_unit("stress_fit", self.stress_fit)
        if not self.reasons or any(not reason.strip() for reason in self.reasons):
            raise ValueError("reasons must contain non-blank reason codes")


@dataclass(frozen=True, slots=True)
class _StrategyRegimePolicy:
    structure_axis: str | None
    volatility_axes: tuple[str, ...] | None


_POLICIES: dict[tuple[str, str], _StrategyRegimePolicy] = {
    ("MOMENTUM", "momentum_v2"): _StrategyRegimePolicy(
        structure_axis="TREND",
        volatility_axes=("NORMAL_VOL", "HIGH_VOL"),
    ),
    ("BREAKOUT", "breakout_v2"): _StrategyRegimePolicy(
        structure_axis="TREND",
        volatility_axes=("NORMAL_VOL", "HIGH_VOL"),
    ),
    ("MEAN_REVERSION", "mean_reversion_v1"): _StrategyRegimePolicy(
        structure_axis="RANGE",
        volatility_axes=("LOW_VOL", "NORMAL_VOL"),
    ),
    # Carry alpha is defined by funding/basis persistence in its own strategy
    # evidence.  SAI-058 has no CARRY axis, so trend/range and volatility are
    # deliberately neutral here; independent liquidity/stress still applies.
    ("CRYPTO_CARRY", "crypto_carry_v1"): _StrategyRegimePolicy(
        structure_axis=None,
        volatility_axes=None,
    ),
}


def gate_strategy_regime(
    strategy: StrategyResultV2,
    regime: RegimeClassificationV2,
) -> StrategyRegimeGateResult:
    """Return versioned point-in-time admission evidence for one candidate."""

    if not isinstance(strategy, StrategyResultV2):
        raise ValueError("strategy must be StrategyResultV2")
    if not isinstance(regime, RegimeClassificationV2):
        raise ValueError("regime must be RegimeClassificationV2")

    identity = (strategy.strategy_family, strategy.strategy_version)
    policy = _POLICIES.get(identity)
    if policy is None:
        return _blocked(strategy, regime, "NO_POLICY_FOR_STRATEGY_VERSION")
    if regime.version != _CLASSIFIER_VERSION:
        return _blocked(strategy, regime, "UNSUPPORTED_CLASSIFIER_VERSION")

    # No unapproved staleness window is invented in this slice.  Candidate and
    # regime evidence must describe the exact same evaluation instant.
    if regime.evaluated_at != strategy.evaluated_at:
        return _blocked(strategy, regime, "POINT_IN_TIME_MISMATCH")
    if strategy.data_quality_state is DataQualityState.BLOCKED:
        return _blocked(strategy, regime, "DATA_QUALITY_BLOCKED")

    structure_fit = _structure_fit(policy.structure_axis, regime)
    volatility_fit = _volatility_fit(policy.volatility_axes, regime)
    stress_fit = _ONE - max(
        regime.liquidity_stress_score,
        regime.stress_probability,
    )

    applicable = [stress_fit]
    if structure_fit is not None:
        applicable.append(structure_fit)
    if volatility_fit is not None:
        applicable.append(volatility_fit)
    compatibility = min(applicable)

    decision = _decision_for_score(compatibility)
    reasons = list(
        _component_reasons(
            structure_fit=structure_fit,
            volatility_fit=volatility_fit,
            stress_fit=stress_fit,
            decision=decision,
        )
    )

    # Data quality is a monotonic safety cap: regime evidence can never turn a
    # degraded candidate into a full ALLOW, while BLOCKED failed closed above.
    if strategy.data_quality_state is DataQualityState.DEGRADED:
        if decision is RegimeGateDecision.ALLOW:
            decision = RegimeGateDecision.REDUCE
        reasons.append("DATA_QUALITY_DEGRADED")

    return StrategyRegimeGateResult(
        policy_version=_POLICY_VERSION,
        classifier_version=regime.version,
        strategy_family=strategy.strategy_family,
        strategy_version=strategy.strategy_version,
        decision=decision,
        compatibility_score=compatibility,
        structure_fit=structure_fit,
        volatility_fit=volatility_fit,
        stress_fit=stress_fit,
        reasons=tuple(reasons),
    )


def _structure_fit(
    axis: str | None,
    regime: RegimeClassificationV2,
) -> Decimal | None:
    if axis is None:
        return None
    if axis == "TREND":
        return regime.trend_probability
    if axis == "RANGE":
        return regime.range_probability
    raise AssertionError(f"unsupported internal structure axis: {axis}")


def _volatility_fit(
    axes: tuple[str, ...] | None,
    regime: RegimeClassificationV2,
) -> Decimal | None:
    if axes is None:
        return None
    values = {
        "LOW_VOL": regime.low_vol_probability,
        "NORMAL_VOL": regime.normal_vol_probability,
        "HIGH_VOL": regime.high_vol_probability,
    }
    return sum((values[axis] for axis in axes), _ZERO)


def _decision_for_score(score: Decimal) -> RegimeGateDecision:
    if score >= _TWO_THIRDS:
        return RegimeGateDecision.ALLOW
    if score >= _ONE_THIRD:
        return RegimeGateDecision.REDUCE
    return RegimeGateDecision.BLOCK


def _component_reasons(
    *,
    structure_fit: Decimal | None,
    volatility_fit: Decimal | None,
    stress_fit: Decimal,
    decision: RegimeGateDecision,
) -> tuple[str, ...]:
    if decision is RegimeGateDecision.ALLOW:
        return ("REGIME_COMPATIBLE",)
    if decision is RegimeGateDecision.REDUCE:
        return ("REGIME_PARTIAL_FIT",)

    reasons: list[str] = []
    if structure_fit is not None and structure_fit < _ONE_THIRD:
        reasons.append("STRUCTURE_INCOMPATIBLE")
    if volatility_fit is not None and volatility_fit < _ONE_THIRD:
        reasons.append("VOLATILITY_INCOMPATIBLE")
    if stress_fit < _ONE_THIRD:
        reasons.append("STRESS_INCOMPATIBLE")
    return tuple(reasons or ("REGIME_INCOMPATIBLE",))


def _blocked(
    strategy: StrategyResultV2,
    regime: RegimeClassificationV2,
    reason: str,
) -> StrategyRegimeGateResult:
    return StrategyRegimeGateResult(
        policy_version=_POLICY_VERSION,
        classifier_version=regime.version,
        strategy_family=strategy.strategy_family,
        strategy_version=strategy.strategy_version,
        decision=RegimeGateDecision.BLOCK,
        compatibility_score=_ZERO,
        structure_fit=None,
        volatility_fit=None,
        stress_fit=_ZERO,
        reasons=(reason,),
    )


def _require_unit(label: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or not _ZERO <= value <= _ONE:
        raise ValueError(f"{label} must be a finite Decimal between 0 and 1")


__all__ = [
    "RegimeGateDecision",
    "StrategyRegimeGateResult",
    "gate_strategy_regime",
]
