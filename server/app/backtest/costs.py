"""Deterministic execution-cost models for offline strategy measurement.

SAI-004 introduced explicit R-unit hooks to prove chronological accounting.
SAI-005 adds a reusable bps-based model for fees, slippage, spread and funding,
plus point-in-time venue overrides and stress scenarios.

This module is measurement-only. It is not imported by the production scanner,
admission, risk, notification, paper lifecycle or execution path.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal


_BPS = Decimal("10000")
_HALF = Decimal("0.5")


def _require_non_negative(name: str, value: Decimal) -> None:
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def _notional_cost_r(*, price: Decimal, risk_per_unit: Decimal, bps: Decimal) -> Decimal:
    if price <= 0:
        raise ValueError("price must be positive")
    if risk_per_unit <= 0:
        raise ValueError("risk_per_unit must be positive")
    _require_non_negative("bps", bps)
    return (price / risk_per_unit) * (bps / _BPS)


@dataclass(frozen=True, slots=True)
class CostModel:
    """Auditable bps assumptions for one venue/time regime.

    ``spread_bps`` is a full quoted spread. Half is charged on entry and half
    on exit. Funding is a conservative non-negative charge per interval.
    """

    maker_fee_bps: Decimal
    taker_fee_bps: Decimal
    entry_slippage_bps: Decimal
    exit_slippage_bps: Decimal
    funding_bps_per_interval: Decimal
    spread_bps: Decimal

    def __post_init__(self) -> None:
        for name in (
            "maker_fee_bps",
            "taker_fee_bps",
            "entry_slippage_bps",
            "exit_slippage_bps",
            "funding_bps_per_interval",
            "spread_bps",
        ):
            _require_non_negative(name, getattr(self, name))

    def stressed(self, multiplier: Decimal) -> "CostModel":
        if multiplier <= 0:
            raise ValueError("stress multiplier must be positive")
        return replace(
            self,
            maker_fee_bps=self.maker_fee_bps * multiplier,
            taker_fee_bps=self.taker_fee_bps * multiplier,
            entry_slippage_bps=self.entry_slippage_bps * multiplier,
            exit_slippage_bps=self.exit_slippage_bps * multiplier,
            funding_bps_per_interval=self.funding_bps_per_interval * multiplier,
            spread_bps=self.spread_bps * multiplier,
        )

    def round_trip_bps(
        self,
        *,
        entry_maker: bool,
        exit_maker: bool,
        funding_intervals: int = 0,
    ) -> Decimal:
        """Project full entry→exit friction in basis points.

        This is the bps-space counterpart of the existing R-unit helpers.  It
        lets research/admission code carry one auditable all-in cost number
        into a candidate without coupling strategy code back to ``CostModel``.
        The quoted spread is counted once across the round trip: half on entry
        and half on exit, matching ``entry_friction_r`` / ``exit_friction_r``.
        """

        if funding_intervals < 0:
            raise ValueError("funding intervals must be non-negative")
        entry_fee = self.maker_fee_bps if entry_maker else self.taker_fee_bps
        exit_fee = self.maker_fee_bps if exit_maker else self.taker_fee_bps
        return (
            entry_fee
            + exit_fee
            + self.entry_slippage_bps
            + self.exit_slippage_bps
            + self.spread_bps
            + self.funding_bps_per_interval * Decimal(funding_intervals)
        )

    def fee_cost_r(
        self, *, price: Decimal, risk_per_unit: Decimal, maker: bool
    ) -> Decimal:
        fee_bps = self.maker_fee_bps if maker else self.taker_fee_bps
        return _notional_cost_r(price=price, risk_per_unit=risk_per_unit, bps=fee_bps)

    def entry_friction_r(self, *, price: Decimal, risk_per_unit: Decimal) -> Decimal:
        return _notional_cost_r(
            price=price,
            risk_per_unit=risk_per_unit,
            bps=self.entry_slippage_bps + self.spread_bps * _HALF,
        )

    def exit_friction_r(self, *, price: Decimal, risk_per_unit: Decimal) -> Decimal:
        return _notional_cost_r(
            price=price,
            risk_per_unit=risk_per_unit,
            bps=self.exit_slippage_bps + self.spread_bps * _HALF,
        )

    def entry_cost_r(
        self, *, price: Decimal, risk_per_unit: Decimal, maker: bool
    ) -> Decimal:
        return self.fee_cost_r(
            price=price, risk_per_unit=risk_per_unit, maker=maker
        ) + self.entry_friction_r(price=price, risk_per_unit=risk_per_unit)

    def exit_cost_r(
        self, *, price: Decimal, risk_per_unit: Decimal, maker: bool
    ) -> Decimal:
        return self.fee_cost_r(
            price=price, risk_per_unit=risk_per_unit, maker=maker
        ) + self.exit_friction_r(price=price, risk_per_unit=risk_per_unit)

    def funding_cost_r(
        self,
        *,
        reference_price: Decimal,
        risk_per_unit: Decimal,
        intervals: int,
    ) -> Decimal:
        if intervals < 0:
            raise ValueError("funding intervals must be non-negative")
        return _notional_cost_r(
            price=reference_price,
            risk_per_unit=risk_per_unit,
            bps=self.funding_bps_per_interval * Decimal(intervals),
        )


@dataclass(frozen=True, slots=True)
class HistoricalCostOverride:
    """Point-in-time replacement for a venue's default cost assumptions."""

    effective_from: datetime
    effective_to: datetime
    model: CostModel
    source_ref: str

    def __post_init__(self) -> None:
        if self.effective_from.tzinfo is None or self.effective_to.tzinfo is None:
            raise ValueError("historical cost override times must be timezone-aware")
        if self.effective_to <= self.effective_from:
            raise ValueError("historical cost override must have a positive interval")
        if not self.source_ref.strip():
            raise ValueError("historical cost override source_ref is required")

    def contains(self, at: datetime) -> bool:
        if at.tzinfo is None:
            raise ValueError("cost resolution time must be timezone-aware")
        return self.effective_from <= at < self.effective_to


@dataclass(frozen=True, slots=True)
class ResolvedCostModel:
    venue: str
    model: CostModel
    source_ref: str
    effective_at: datetime


@dataclass(frozen=True, slots=True)
class VenueCostProfile:
    """Venue-specific default plus non-overlapping historical replacements."""

    venue: str
    default: CostModel
    overrides: tuple[HistoricalCostOverride, ...] = ()

    def __post_init__(self) -> None:
        if not self.venue.strip():
            raise ValueError("venue is required")
        ordered = sorted(self.overrides, key=lambda item: item.effective_from)
        if tuple(ordered) != self.overrides:
            raise ValueError("historical cost overrides must be ordered")
        for previous, current in zip(ordered, ordered[1:]):
            if current.effective_from < previous.effective_to:
                raise ValueError("historical cost overrides overlap")

    def resolve(
        self,
        at: datetime,
        *,
        stress_multiplier: Decimal = Decimal(1),
    ) -> ResolvedCostModel:
        if at.tzinfo is None:
            raise ValueError("cost resolution time must be timezone-aware")
        selected = self.default
        source_ref = f"venue-default:{self.venue}"
        for override in self.overrides:
            if override.contains(at):
                selected = override.model
                source_ref = override.source_ref
                break
        if stress_multiplier != Decimal(1):
            selected = selected.stressed(stress_multiplier)
            source_ref = f"{source_ref};stress=x{stress_multiplier}"
        return ResolvedCostModel(
            venue=self.venue,
            model=selected,
            source_ref=source_ref,
            effective_at=at,
        )


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
    """Deterministic test/replay charges expressed directly in R units."""

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


__all__ = [
    "CostModel",
    "ExplicitCostSchedule",
    "FundingCharge",
    "HistoricalCostOverride",
    "ResolvedCostModel",
    "VenueCostProfile",
    "ZERO_COSTS",
]
