"""Explicit cost hooks for the SAI-004 golden-engine skeleton.

Amounts are supplied in R units on purpose.  SAI-005 introduces the actual
venue-aware CostModel (fees/slippage/spread/funding in bps, historical
overrides and stress multipliers).  This module only proves that the event
engine accounts for costs in chronological order and reports net != gross.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class FundingCharge:
    at: datetime
    amount_r: Decimal

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise ValueError("funding time must be timezone-aware")
        if self.amount_r < 0:
            raise ValueError("funding charge must not be negative")


@dataclass(frozen=True, slots=True)
class ExplicitCostSchedule:
    """Deterministic test/replay charges, not the production CostModel."""

    entry_fee_r: Decimal = Decimal(0)
    entry_slippage_r: Decimal = Decimal(0)
    exit_fee_r: Decimal = Decimal(0)
    exit_slippage_r: Decimal = Decimal(0)
    funding: tuple[FundingCharge, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "entry_fee_r",
            "entry_slippage_r",
            "exit_fee_r",
            "exit_slippage_r",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        times = [charge.at for charge in self.funding]
        if times != sorted(times):
            raise ValueError("funding charges must be ordered by time")


ZERO_COSTS = ExplicitCostSchedule()


__all__ = ["ExplicitCostSchedule", "FundingCharge", "ZERO_COSTS"]
