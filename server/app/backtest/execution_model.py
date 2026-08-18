"""Deterministic execution constraints for the golden backtest skeleton.

This is intentionally small: SAI-004 needs reproducible reject/no-fill/partial
fill behaviour.  Venue-specific fee/slippage and historical liquidity models
belong to SAI-005 and later execution work.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from ..models.enums import Direction


@dataclass(frozen=True, slots=True)
class VenueConstraints:
    quantity_step: Decimal
    min_quantity: Decimal
    max_fill_quantity: Decimal | None = None
    liquidation_price: Decimal | None = None

    def __post_init__(self) -> None:
        if self.quantity_step <= 0:
            raise ValueError("quantity_step must be positive")
        if self.min_quantity <= 0:
            raise ValueError("min_quantity must be positive")
        if self.max_fill_quantity is not None and self.max_fill_quantity < 0:
            raise ValueError("max_fill_quantity must not be negative")


def rejection_reason(
    *,
    direction: Direction,
    entry: Decimal,
    stop: Decimal,
    requested_quantity: Decimal,
    constraints: VenueConstraints,
) -> str | None:
    if requested_quantity < constraints.min_quantity:
        return "requested quantity below venue minimum"
    if requested_quantity % constraints.quantity_step != 0:
        return "requested quantity not aligned to venue quantity step"

    liquidation = constraints.liquidation_price
    if liquidation is not None:
        if direction is Direction.LONG and liquidation >= stop:
            return "liquidation can occur before the protective stop"
        if direction is Direction.SHORT and liquidation <= stop:
            return "liquidation can occur before the protective stop"
        if direction is Direction.LONG and liquidation >= entry:
            return "liquidation price is invalid for a long position"
        if direction is Direction.SHORT and liquidation <= entry:
            return "liquidation price is invalid for a short position"
    return None


def deterministic_fill_quantity(
    requested_quantity: Decimal, constraints: VenueConstraints
) -> Decimal:
    """Fill once up to an explicit liquidity cap; cancel any remainder.

    The cap is a deterministic fixture input, not an inferred market model.
    Remainders are deliberately not replayed on later bars in SAI-004.
    """

    cap = constraints.max_fill_quantity
    if cap is None or cap >= requested_quantity:
        return requested_quantity
    steps = (cap / constraints.quantity_step).to_integral_value(rounding=ROUND_DOWN)
    quantity = steps * constraints.quantity_step
    if quantity < constraints.min_quantity:
        return Decimal(0)
    return quantity


__all__ = ["VenueConstraints", "deterministic_fill_quantity", "rejection_reason"]
