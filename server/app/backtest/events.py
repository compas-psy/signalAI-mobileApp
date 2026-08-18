"""Immutable events for the deterministic golden backtest.

The engine records decisions in production order instead of deriving a result
from an idealised candle close.  Timestamps are UTC-aware and every event is
append-only inside the returned result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class EventKind(StrEnum):
    SIGNAL_AVAILABLE = "SIGNAL_AVAILABLE"
    ORDER_REJECTED = "ORDER_REJECTED"
    NO_FILL = "NO_FILL"
    ENTRY_FILL = "ENTRY_FILL"
    ENTRY_PARTIAL_FILL = "ENTRY_PARTIAL_FILL"
    STOP = "STOP"
    TARGET = "TARGET"
    TRAILING_STOP_MOVED = "TRAILING_STOP_MOVED"
    TIMEOUT = "TIMEOUT"
    FEE = "FEE"
    SLIPPAGE = "SLIPPAGE"
    FUNDING = "FUNDING"


@dataclass(frozen=True, slots=True)
class MarketBar:
    """Closed execution bar made available to the golden engine."""

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is None:
            raise ValueError("backtest bar time must be timezone-aware")
        if self.high < self.low:
            raise ValueError("bar high must not be below low")
        if self.high < max(self.open, self.close):
            raise ValueError("bar high must contain open and close")
        if self.low > min(self.open, self.close):
            raise ValueError("bar low must contain open and close")


@dataclass(frozen=True, slots=True)
class BacktestEvent:
    at: datetime
    kind: EventKind
    price: Decimal | None = None
    quantity: Decimal | None = None
    amount_r: Decimal | None = None
    target_index: int | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise ValueError("backtest event time must be timezone-aware")
        if self.quantity is not None and self.quantity < 0:
            raise ValueError("event quantity must not be negative")
        if self.amount_r is not None and self.amount_r < 0:
            raise ValueError("cost amount_r must not be negative")


__all__ = ["BacktestEvent", "EventKind", "MarketBar"]
