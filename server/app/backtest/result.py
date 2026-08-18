"""Immutable result of one golden event-driven replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .events import BacktestEvent


class BacktestOutcome(StrEnum):
    OPEN = "OPEN"
    NO_FILL = "NO_FILL"
    STOP = "STOP"
    TARGETS = "TARGETS"
    TIMEOUT = "TIMEOUT"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class BacktestResult:
    outcome: BacktestOutcome
    events: tuple[BacktestEvent, ...]
    filled_quantity: Decimal
    gross_r: Decimal
    cost_r: Decimal
    net_r: Decimal
    current_stop: Decimal
    targets_hit: int
    entry_time: datetime | None = None
    close_time: datetime | None = None

    def __post_init__(self) -> None:
        if self.filled_quantity < 0:
            raise ValueError("filled_quantity must not be negative")
        if self.cost_r < 0:
            raise ValueError("cost_r must not be negative")
        if self.net_r != self.gross_r - self.cost_r:
            raise ValueError("net_r must equal gross_r - cost_r")
        if self.entry_time is not None and self.entry_time.tzinfo is None:
            raise ValueError("entry_time must be timezone-aware")
        if self.close_time is not None and self.close_time.tzinfo is None:
            raise ValueError("close_time must be timezone-aware")


__all__ = ["BacktestOutcome", "BacktestResult"]
