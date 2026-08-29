from __future__ import annotations

from datetime import UTC, datetime

from app.backtest.readiness import assess_history_bounds
from app.scheduler.lanes import HEAVY_JOB_NAMES, SchedulerLane, select_job_names


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def test_backtest_readiness_fails_closed_when_history_is_short() -> None:
    readiness = assess_history_bounds(
        venue="BYBIT",
        now=NOW,
        required_months=36,
        earliest_d1=datetime(2026, 1, 1, tzinfo=UTC),
        latest_d1=datetime(2026, 8, 28, tzinfo=UTC),
        earliest_h1=datetime(2026, 8, 1, tzinfo=UTC),
        latest_h1=datetime(2026, 8, 29, 11, tzinfo=UTC),
    )

    assert readiness.ready is False
    assert readiness.status == "INSUFFICIENT_HISTORY"
    assert readiness.required_months == 36
    assert readiness.available_h1_months < 36
    assert "H1_HISTORY_TOO_SHORT" in readiness.blockers


def test_backtest_readiness_accepts_complete_36_month_window() -> None:
    readiness = assess_history_bounds(
        venue="FORTS",
        now=NOW,
        required_months=36,
        earliest_d1=datetime(2023, 7, 1, tzinfo=UTC),
        latest_d1=datetime(2026, 8, 28, tzinfo=UTC),
        earliest_h1=datetime(2023, 7, 1, tzinfo=UTC),
        latest_h1=datetime(2026, 8, 29, 11, tzinfo=UTC),
    )

    assert readiness.ready is True
    assert readiness.status == "READY"
    assert readiness.blockers == ()


def test_entry_backtest_job_is_heavy_only() -> None:
    assert "entry-backtest" in HEAVY_JOB_NAMES
    names = ("scan", "shadow", "paper_ab", "portfolio", "research", "entry-backtest")

    assert "entry-backtest" in select_job_names(names, SchedulerLane.HEAVY)
    assert "entry-backtest" not in select_job_names(names, SchedulerLane.MARKET)
