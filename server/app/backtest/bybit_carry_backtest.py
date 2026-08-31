"""Point-in-time realized outcome and OOS replay for Bybit hedged carry.

Carry is not a directional price-return strategy. Its realized outcome is
measured in basis points of position notional: funding received/paid, plus the
change in perpetual-vs-index basis for the hedged pair, less explicit
round-trip execution and hedge carry costs.

The predictive uncertainty haircut used by ``crypto_carry_v1`` is deliberately
not deducted here. It belongs to admission/evidence, not realized P&L.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from ..market.derivatives import CryptoCarryMarketFacts, FundingObservation
from ..models.enums import Direction
from ..strategies.crypto_carry_v1 import evaluate_crypto_carry_v1

_BPS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class CarryOutcome:
    """Realized hedged-carry outcome in bps of position notional."""

    funding_bps: Decimal
    basis_bps: Decimal
    cost_bps: Decimal
    net_bps: Decimal
    funding_events: int


@dataclass(frozen=True, slots=True)
class CarryReplayGate:
    """Acceptance gate in the carry metric space, never directional R."""

    min_trades: int = 200
    min_profit_factor: Decimal = Decimal("1.20")
    min_expectancy_bps: Decimal = Decimal("1.00")
    max_top5_contribution: Decimal = Decimal("0.30")


@dataclass(frozen=True, slots=True)
class CarryReplayReport:
    metric_space: str
    outcomes: tuple[CarryOutcome, ...]
    expectancy_bps: Decimal
    profit_factor: Decimal
    top5_contribution: Decimal
    gate_passed: bool
    gate_reasons: tuple[str, ...]


def calculate_carry_outcome(
    *,
    direction: Direction,
    entry_at: datetime,
    exit_at: datetime,
    entry_mark: Decimal,
    entry_index: Decimal,
    exit_mark: Decimal,
    exit_index: Decimal,
    funding_history: tuple[FundingObservation, ...],
    round_trip_cost_bps: Decimal,
    hedge_carry_bps_per_interval: Decimal,
    funding_interval_minutes: int,
) -> CarryOutcome:
    """Calculate realized carry without introducing portfolio-return semantics.

    A short perpetual paired with long spot/index earns positive funding when
    funding is positive; a long perpetual paired with a short hedge earns it
    when funding is negative. The hedge's price P&L is the opposite change in
    perp-vs-index basis, so the pair realizes ``basis_0 - basis_1`` for SHORT
    and ``basis_1 - basis_0`` for LONG.

    Funding is counted only for settlements strictly after entry and at/before
    exit. This avoids claiming a funding payment at the exact entry timestamp
    when the historical execution timestamp cannot establish whether the
    position was included in that settlement.
    """

    _require_aware(entry_at, "entry_at")
    _require_aware(exit_at, "exit_at")
    if exit_at <= entry_at:
        raise ValueError("exit_at must be after entry_at")
    if not isinstance(direction, Direction):
        raise ValueError("direction must be Direction")
    for name, value in (
        ("entry_mark", entry_mark),
        ("entry_index", entry_index),
        ("exit_mark", exit_mark),
        ("exit_index", exit_index),
        ("round_trip_cost_bps", round_trip_cost_bps),
        ("hedge_carry_bps_per_interval", hedge_carry_bps_per_interval),
    ):
        _require_finite_decimal(name, value)
    if entry_mark <= 0 or entry_index <= 0 or exit_mark <= 0 or exit_index <= 0:
        raise ValueError("carry prices must be positive")
    if round_trip_cost_bps < 0 or hedge_carry_bps_per_interval < 0:
        raise ValueError("carry costs must be non-negative")
    if isinstance(funding_interval_minutes, bool) or funding_interval_minutes <= 0:
        raise ValueError("funding_interval_minutes must be positive")

    settlements = tuple(
        item
        for item in funding_history
        if entry_at < item.settled_at <= exit_at
        and item.tradable_at <= exit_at
    )
    position_sign = Decimal(1) if direction is Direction.LONG else Decimal(-1)
    funding_bps = sum(
        (-position_sign * item.rate * _BPS for item in settlements),
        Decimal(0),
    )

    entry_basis = (entry_mark - entry_index) / entry_index
    exit_basis = (exit_mark - exit_index) / exit_index
    if direction is Direction.SHORT:
        basis_bps = (entry_basis - exit_basis) * _BPS
    else:
        basis_bps = (exit_basis - entry_basis) * _BPS

    interval_count = len(settlements)
    hedge_carry_bps = hedge_carry_bps_per_interval * Decimal(interval_count)
    cost_bps = round_trip_cost_bps + hedge_carry_bps
    net_bps = funding_bps + basis_bps - cost_bps

    return CarryOutcome(
        funding_bps=funding_bps,
        basis_bps=basis_bps,
        cost_bps=cost_bps,
        net_bps=net_bps,
        funding_events=interval_count,
    )


def replay_carry_oos(
    *,
    facts: tuple[CryptoCarryMarketFacts, ...],
    execution_cost_bps: Decimal,
    hedge_carry_bps_per_interval: Decimal,
    funding_uncertainty_bps_per_interval: Decimal,
    gate: CarryReplayGate = CarryReplayGate(),
) -> CarryReplayReport:
    """Replay carry candidates using only immutable PIT market facts.

    The input facts must already be resolved from one immutable snapshot and
    ordered by ``evaluated_at``. The strategy sees only each fact's funding
    history up to that decision time. Future facts are used only to settle an
    already-created candidate at its deterministic three-funding-interval
    horizon. Positions are non-overlapping per instrument in this replay.
    """

    if not facts:
        return _report((), gate)
    ordered = tuple(sorted(facts, key=lambda item: item.tradable_at))
    outcomes: list[CarryOutcome] = []
    next_available_at: dict[str, datetime] = {}

    for index, current in enumerate(ordered):
        if current.tradable_at < next_available_at.get(current.instrument_id, current.tradable_at):
            continue

        candidate = evaluate_crypto_carry_v1(
            facts=current,
            execution_cost_bps=execution_cost_bps,
            hedge_carry_bps_per_interval=hedge_carry_bps_per_interval,
            funding_uncertainty_bps_per_interval=funding_uncertainty_bps_per_interval,
            evaluated_at=current.tradable_at,
        )
        if candidate is None:
            continue

        horizon_minutes = candidate.horizon.value
        if candidate.horizon.unit != "MINUTES":
            raise ValueError("crypto_carry_v1 horizon must be MINUTES")
        target_exit = current.tradable_at + timedelta(minutes=horizon_minutes)
        exit_fact = next(
            (item for item in ordered[index + 1 :] if item.instrument_id == current.instrument_id and item.tradable_at >= target_exit),
            None,
        )
        if exit_fact is None:
            continue

        next_available_at[current.instrument_id] = exit_fact.tradable_at
        outcomes.append(
            calculate_carry_outcome(
                direction=candidate.direction,
                entry_at=current.tradable_at,
                exit_at=exit_fact.tradable_at,
                entry_mark=current.mark_price,
                entry_index=current.index_price,
                exit_mark=exit_fact.mark_price,
                exit_index=exit_fact.index_price,
                funding_history=exit_fact.funding_history,
                round_trip_cost_bps=execution_cost_bps,
                hedge_carry_bps_per_interval=hedge_carry_bps_per_interval,
                funding_interval_minutes=current.funding_interval_minutes,
            )
        )

    return _report(tuple(outcomes), gate)


def _report(
    outcomes: tuple[CarryOutcome, ...], gate: CarryReplayGate
) -> CarryReplayReport:
    values = tuple(item.net_bps for item in outcomes)
    if not values:
        expectancy = Decimal(0)
        profit_factor = Decimal(0)
        top5 = Decimal(0)
    else:
        expectancy = sum(values, Decimal(0)) / Decimal(len(values))
        positive = sum((value for value in values if value > 0), Decimal(0))
        negative = abs(sum((value for value in values if value < 0), Decimal(0)))
        profit_factor = Decimal("Infinity") if negative == 0 and positive > 0 else (
            positive / negative if negative > 0 else Decimal(0)
        )
        positive_values = sorted((value for value in values if value > 0), reverse=True)
        top5 = (
            sum(positive_values[:5], Decimal(0)) / positive
            if positive > 0
            else Decimal(0)
        )

    reasons: list[str] = []
    if len(outcomes) < gate.min_trades:
        reasons.append(f"MIN_TRADES:{len(outcomes)}<{gate.min_trades}")
    if profit_factor < gate.min_profit_factor:
        reasons.append(f"PROFIT_FACTOR:{profit_factor}<{gate.min_profit_factor}")
    if expectancy < gate.min_expectancy_bps:
        reasons.append(f"EXPECTANCY_BPS:{expectancy}<{gate.min_expectancy_bps}")
    if top5 > gate.max_top5_contribution:
        reasons.append(f"TOP5_CONTRIBUTION:{top5}>{gate.max_top5_contribution}")

    return CarryReplayReport(
        metric_space="CARRY_BPS",
        outcomes=outcomes,
        expectancy_bps=expectancy,
        profit_factor=profit_factor,
        top5_contribution=top5,
        gate_passed=not reasons,
        gate_reasons=tuple(reasons),
    )


def _require_finite_decimal(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "CarryOutcome",
    "CarryReplayGate",
    "CarryReplayReport",
    "calculate_carry_outcome",
    "replay_carry_oos",
]
