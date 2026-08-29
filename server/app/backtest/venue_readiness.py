"""Fail-closed history readiness for venue strategy backtests.

A backtest that lacks the configured calendar history must be BLOCKED rather
than represented as a zero-return run.  This module is measurement-only and has
no scanner, paper, risk, execution or promotion side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True, slots=True)
class HistoryReadiness:
    ready: bool
    status: str
    required_months: int
    available_months: int
    period_from: datetime
    period_to: datetime


def _complete_calendar_months(start: datetime, end: datetime) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    start_tail = (
        start.day,
        start.hour,
        start.minute,
        start.second,
        start.microsecond,
    )
    end_tail = (
        end.day,
        end.hour,
        end.minute,
        end.second,
        end.microsecond,
    )
    if end_tail < start_tail:
        months -= 1
    return max(0, months)


def assess_history_readiness(
    observed_times: Iterable[datetime],
    *,
    min_history_months: int,
) -> HistoryReadiness:
    """Assess an already point-in-time-bounded chronological history stream."""

    if isinstance(min_history_months, bool) or min_history_months <= 0:
        raise ValueError("min_history_months must be a positive integer")
    times = tuple(observed_times)
    if len(times) < 2:
        raise ValueError("history readiness requires at least two observations")
    if any(
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        for value in times
    ):
        raise ValueError("history observations must be timezone-aware datetimes")
    if list(times) != sorted(times):
        raise ValueError("history observations must be chronological")
    if len(times) != len(set(times)):
        raise ValueError("history observations must not duplicate timestamps")

    period_from = times[0]
    period_to = times[-1]
    available = _complete_calendar_months(period_from, period_to)
    ready = available >= min_history_months
    return HistoryReadiness(
        ready=ready,
        status="DATA_READY" if ready else "BLOCKED_INSUFFICIENT_HISTORY",
        required_months=min_history_months,
        available_months=available,
        period_from=period_from,
        period_to=period_to,
    )


__all__ = ["HistoryReadiness", "assess_history_readiness"]
