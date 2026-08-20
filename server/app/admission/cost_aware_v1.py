"""Cost-aware admission and ranking for the R4 candidate stack.

The contract stays in basis-point space so expected edge and friction are
comparable.  It consumes the canonical SAI-005 ``ResolvedCostModel`` and the
SAI-059 regime gate, then applies the accepted B10.3 rule:

    expected gross edge
    - fees / slippage / spread / funding
    - explicit carry or hedge cost
    - liquidity penalty
    = expected net edge

A candidate is not actionable unless expected net edge strictly exceeds the
explicit uncertainty budget.  This layer owns neither risk sizing nor orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from ..backtest.costs import ResolvedCostModel
from ..regime.strategy_gate_v1 import (
    RegimeGateDecision,
    StrategyRegimeGateResult,
)
from ..strategies.result_v2 import StrategyResultV2

_POLICY_VERSION = "cost_aware_admission_v1"
_SUPPORTED_REGIME_POLICY = "strategy_regime_gate_v1"
_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class CostAwareCandidateInput:
    """Explicit point-in-time decomposition of one candidate's expected edge."""

    candidate_key: str
    strategy: StrategyResultV2
    regime_gate: StrategyRegimeGateResult
    resolved_cost_model: ResolvedCostModel
    expected_gross_edge_bps: Decimal
    expected_carry_cost_bps: Decimal
    liquidity_penalty_bps: Decimal
    uncertainty_bps: Decimal
    funding_intervals: int = 0
    entry_maker: bool = False
    exit_maker: bool = False

    def __post_init__(self) -> None:
        _require_text("candidate_key", self.candidate_key)
        if not isinstance(self.strategy, StrategyResultV2):
            raise ValueError("strategy must be StrategyResultV2")
        if not isinstance(self.regime_gate, StrategyRegimeGateResult):
            raise ValueError("regime_gate must be StrategyRegimeGateResult")
        if not isinstance(self.resolved_cost_model, ResolvedCostModel):
            raise ValueError("resolved_cost_model must be ResolvedCostModel")
        for label in (
            "expected_gross_edge_bps",
            "expected_carry_cost_bps",
            "liquidity_penalty_bps",
            "uncertainty_bps",
        ):
            _require_non_negative_decimal(label, getattr(self, label))
        if isinstance(self.funding_intervals, bool) or self.funding_intervals < 0:
            raise ValueError("funding_intervals must be a non-negative integer")
        if not isinstance(self.funding_intervals, int):
            raise ValueError("funding_intervals must be a non-negative integer")
        if not isinstance(self.entry_maker, bool) or not isinstance(self.exit_maker, bool):
            raise ValueError("entry_maker and exit_maker must be bool")
        _require_text("cost source_ref", self.resolved_cost_model.source_ref)
        _require_aware_datetime("cost effective_at", self.resolved_cost_model.effective_at)


@dataclass(frozen=True, slots=True)
class CostAwareAdmissionResult:
    """Auditable edge/cost evidence, never a risk or execution instruction."""

    cost_policy_version: str
    candidate_key: str
    strategy_family: str
    strategy_version: str
    decision: RegimeGateDecision
    raw_edge_score: Decimal
    regime_compatibility_score: Decimal
    venue: str
    cost_source_ref: str
    expected_gross_edge_bps: Decimal
    expected_fee_bps: Decimal
    expected_slippage_bps: Decimal
    expected_spread_bps: Decimal
    expected_funding_cost_bps: Decimal
    expected_execution_cost_bps: Decimal
    expected_carry_cost_bps: Decimal
    liquidity_penalty_bps: Decimal
    expected_total_cost_bps: Decimal
    expected_net_edge_bps: Decimal
    uncertainty_bps: Decimal
    edge_surplus_bps: Decimal
    cost_survival_ratio: Decimal
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text("cost_policy_version", self.cost_policy_version)
        _require_text("candidate_key", self.candidate_key)
        _require_text("strategy_family", self.strategy_family)
        _require_text("strategy_version", self.strategy_version)
        _require_text("venue", self.venue)
        _require_text("cost_source_ref", self.cost_source_ref)
        if not isinstance(self.decision, RegimeGateDecision):
            raise ValueError("decision must be RegimeGateDecision")
        _require_finite_decimal("raw_edge_score", self.raw_edge_score)
        _require_unit_decimal(
            "regime_compatibility_score", self.regime_compatibility_score
        )
        for label in (
            "expected_gross_edge_bps",
            "expected_fee_bps",
            "expected_slippage_bps",
            "expected_spread_bps",
            "expected_funding_cost_bps",
            "expected_execution_cost_bps",
            "expected_carry_cost_bps",
            "liquidity_penalty_bps",
            "expected_total_cost_bps",
            "uncertainty_bps",
        ):
            _require_non_negative_decimal(label, getattr(self, label))
        _require_finite_decimal("expected_net_edge_bps", self.expected_net_edge_bps)
        _require_finite_decimal("edge_surplus_bps", self.edge_surplus_bps)
        _require_unit_decimal("cost_survival_ratio", self.cost_survival_ratio)
        if not self.reasons or any(not reason.strip() for reason in self.reasons):
            raise ValueError("reasons must contain non-blank reason codes")


def evaluate_cost_aware_admission(
    candidate: CostAwareCandidateInput,
) -> CostAwareAdmissionResult:
    """Evaluate one candidate using explicit, auditable bps-space costs."""

    if not isinstance(candidate, CostAwareCandidateInput):
        raise ValueError("candidate must be CostAwareCandidateInput")

    strategy = candidate.strategy
    gate = candidate.regime_gate
    resolved = candidate.resolved_cost_model
    model = resolved.model

    entry_fee = model.maker_fee_bps if candidate.entry_maker else model.taker_fee_bps
    exit_fee = model.maker_fee_bps if candidate.exit_maker else model.taker_fee_bps
    fee_bps = entry_fee + exit_fee
    slippage_bps = model.entry_slippage_bps + model.exit_slippage_bps
    spread_bps = model.spread_bps
    funding_bps = model.funding_bps_per_interval * Decimal(candidate.funding_intervals)
    execution_cost_bps = model.round_trip_bps(
        entry_maker=candidate.entry_maker,
        exit_maker=candidate.exit_maker,
        funding_intervals=candidate.funding_intervals,
    )
    component_sum = fee_bps + slippage_bps + spread_bps + funding_bps
    if execution_cost_bps != component_sum:
        raise AssertionError("canonical CostModel round-trip components do not reconcile")

    total_cost_bps = (
        execution_cost_bps
        + candidate.expected_carry_cost_bps
        + candidate.liquidity_penalty_bps
    )
    net_edge_bps = candidate.expected_gross_edge_bps - total_cost_bps
    edge_surplus_bps = net_edge_bps - candidate.uncertainty_bps
    cost_survival_ratio = _cost_survival_ratio(
        gross=candidate.expected_gross_edge_bps,
        net=net_edge_bps,
    )

    identity_matches = (
        gate.strategy_family == strategy.strategy_family
        and gate.strategy_version == strategy.strategy_version
    )
    if not identity_matches:
        decision = RegimeGateDecision.BLOCK
        reasons = ("STRATEGY_GATE_IDENTITY_MISMATCH",)
    elif gate.policy_version != _SUPPORTED_REGIME_POLICY:
        decision = RegimeGateDecision.BLOCK
        reasons = ("UNSUPPORTED_REGIME_GATE_POLICY",)
    elif resolved.effective_at != strategy.evaluated_at:
        decision = RegimeGateDecision.BLOCK
        reasons = ("COST_POINT_IN_TIME_MISMATCH",)
    elif gate.decision is RegimeGateDecision.BLOCK:
        decision = RegimeGateDecision.BLOCK
        reasons = ("REGIME_BLOCKED",)
    elif net_edge_bps <= candidate.uncertainty_bps:
        decision = RegimeGateDecision.BLOCK
        reasons = ("NET_EDGE_NOT_ABOVE_UNCERTAINTY",)
    elif gate.decision is RegimeGateDecision.REDUCE:
        decision = RegimeGateDecision.REDUCE
        reasons = ("NET_EDGE_ABOVE_UNCERTAINTY", "REGIME_REDUCED")
    else:
        decision = RegimeGateDecision.ALLOW
        reasons = ("NET_EDGE_ABOVE_UNCERTAINTY",)

    return CostAwareAdmissionResult(
        cost_policy_version=_POLICY_VERSION,
        candidate_key=candidate.candidate_key,
        strategy_family=strategy.strategy_family,
        strategy_version=strategy.strategy_version,
        decision=decision,
        raw_edge_score=strategy.raw_edge_score,
        regime_compatibility_score=gate.compatibility_score,
        venue=resolved.venue,
        cost_source_ref=resolved.source_ref,
        expected_gross_edge_bps=candidate.expected_gross_edge_bps,
        expected_fee_bps=fee_bps,
        expected_slippage_bps=slippage_bps,
        expected_spread_bps=spread_bps,
        expected_funding_cost_bps=funding_bps,
        expected_execution_cost_bps=execution_cost_bps,
        expected_carry_cost_bps=candidate.expected_carry_cost_bps,
        liquidity_penalty_bps=candidate.liquidity_penalty_bps,
        expected_total_cost_bps=total_cost_bps,
        expected_net_edge_bps=net_edge_bps,
        uncertainty_bps=candidate.uncertainty_bps,
        edge_surplus_bps=edge_surplus_bps,
        cost_survival_ratio=cost_survival_ratio,
        reasons=reasons,
    )


def rank_cost_aware_candidates(
    candidates: Sequence[CostAwareCandidateInput],
) -> tuple[CostAwareAdmissionResult, ...]:
    """Rank actionable candidates without overriding regime admission semantics.

    BLOCK/no-signal candidates are absent.  ALLOW stays ahead of REDUCE so a
    large nominal edge cannot silently bypass the regime gate.  Within the same
    admission tier, the primary rank is edge remaining *after* explicit costs
    and uncertainty; cost survival and raw strategy edge are deterministic
    tie-breakers rather than a new opaque ensemble score.
    """

    evaluated = [evaluate_cost_aware_admission(item) for item in candidates]
    actionable = [
        item for item in evaluated if item.decision is not RegimeGateDecision.BLOCK
    ]
    decision_rank = {
        RegimeGateDecision.ALLOW: 0,
        RegimeGateDecision.REDUCE: 1,
    }
    return tuple(
        sorted(
            actionable,
            key=lambda item: (
                decision_rank[item.decision],
                -item.edge_surplus_bps,
                -item.cost_survival_ratio,
                -item.raw_edge_score,
                item.candidate_key,
            ),
        )
    )


def _cost_survival_ratio(*, gross: Decimal, net: Decimal) -> Decimal:
    if gross <= _ZERO or net <= _ZERO:
        return _ZERO
    return min(net / gross, _ONE)


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


def _require_aware_datetime(label: str, value: object) -> None:
    if (
        not hasattr(value, "tzinfo")
        or value.tzinfo is None  # type: ignore[union-attr]
        or value.utcoffset() is None  # type: ignore[union-attr]
    ):
        raise ValueError(f"{label} must be timezone-aware")


__all__ = [
    "CostAwareAdmissionResult",
    "CostAwareCandidateInput",
    "evaluate_cost_aware_admission",
    "rank_cost_aware_candidates",
]
