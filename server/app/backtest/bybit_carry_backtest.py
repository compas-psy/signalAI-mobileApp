"""Point-in-time realized outcome for the Bybit hedged-carry challenger.

Carry is not a directional price-return strategy.  Its realized outcome is
measured in basis points of position notional: funding received/paid, plus the
change in perpetual-vs-index basis for the hedged pair, less explicit
round-trip execution and hedge carry costs.

The predictive uncertainty haircut used by ``crypto_carry_v1`` is deliberately
not deducted here.  It belongs to admission/evidence, not realized P&L.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..market.derivatives import FundingObservation
from ..models.enums import Direction

_BPS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class CarryOutcome:
    """Realized hedged-carry outcome in bps of position notional."""

    funding_bps: Decimal
    basis_bps: Decimal
    cost_bps: Decimal
    net_bps: Decimal
    funding_events: int


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
    when funding is negative.  The hedge's price P&L is the opposite change in
    perp-vs-index basis, so the pair realizes ``basis_0 - basis_1`` for SHORT
    and ``basis_1 - basis_0`` for LONG.

    Funding is counted only for settlements strictly after entry and at/before
    exit.  This avoids claiming a funding payment at the exact entry timestamp
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
    hedge_carry_bps = (
        hedge_carry_bps_per_interval * Decimal(interval_count)
    )
    cost_bps = round_trip_cost_bps + hedge_carry_bps
    net_bps = funding_bps + basis_bps - cost_bps

    return CarryOutcome(
        funding_bps=funding_bps,
        basis_bps=basis_bps,
        cost_bps=cost_bps,
        net_bps=net_bps,
        funding_events=interval_count,
    )


def _require_finite_decimal(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = ["CarryOutcome", "calculate_carry_outcome"]
