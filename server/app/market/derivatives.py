"""Venue-neutral derivative-market facts used by candidate strategies.

Provider adapters may populate these immutable facts, but strategy code must not
know how a venue authenticates, transports requests or submits orders.  This
keeps the R4 carry candidate reusable when another venue (for example Lighter)
implements equivalent public market data later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class FundingObservation:
    """One settled funding print with its point-in-time availability boundary."""

    rate: Decimal
    settled_at: datetime
    tradable_at: datetime
    source: str

    def __post_init__(self) -> None:
        _require_finite_decimal("funding rate", self.rate)
        _require_aware_datetime("funding settled_at", self.settled_at)
        _require_aware_datetime("funding tradable_at", self.tradable_at)
        if self.tradable_at < self.settled_at:
            raise ValueError("funding tradable_at must not precede settled_at")
        _require_text("funding source", self.source)


@dataclass(frozen=True, slots=True)
class CryptoCarryMarketFacts:
    """Public market facts sufficient to evaluate a hedged crypto carry edge."""

    instrument_id: str
    mark_price: Decimal
    index_price: Decimal
    current_funding_rate: Decimal
    funding_interval_minutes: int
    funding_history: tuple[FundingObservation, ...]
    observed_at: datetime
    tradable_at: datetime
    source: str

    def __post_init__(self) -> None:
        _require_text("instrument_id", self.instrument_id)
        _require_finite_decimal("mark_price", self.mark_price)
        _require_finite_decimal("index_price", self.index_price)
        _require_finite_decimal("current_funding_rate", self.current_funding_rate)
        if self.mark_price <= 0 or self.index_price <= 0:
            raise ValueError("mark_price and index_price must be positive")
        if isinstance(self.funding_interval_minutes, bool) or self.funding_interval_minutes <= 0:
            raise ValueError("funding_interval_minutes must be positive")
        _require_aware_datetime("observed_at", self.observed_at)
        _require_aware_datetime("tradable_at", self.tradable_at)
        if self.tradable_at < self.observed_at:
            raise ValueError("market facts tradable_at must not precede observed_at")
        _require_text("source", self.source)

    @property
    def mark_index_basis_rate(self) -> Decimal:
        """Signed mark-to-index premium as a fraction of index price."""

        return (self.mark_price - self.index_price) / self.index_price


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")


def _require_finite_decimal(label: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{label} must be a finite Decimal")


def _require_aware_datetime(label: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


__all__ = ["CryptoCarryMarketFacts", "FundingObservation"]
