from __future__ import annotations

from datetime import timedelta

from app.scheduler.heavy import build_heavy_scheduler
from app.scheduler.lanes import HEAVY_JOB_NAMES, SchedulerLane, select_job_names


def test_entry_backtest_is_heavy_only() -> None:
    assert "entry-backtest" in HEAVY_JOB_NAMES
    names = ("universe", "scan", "portfolio", "research", "entry-backtest")

    assert "entry-backtest" in select_job_names(names, SchedulerLane.HEAVY)
    assert "entry-backtest" not in select_job_names(names, SchedulerLane.MARKET)


def test_heavy_scheduler_registers_entry_backtest_after_existing_analytics() -> None:
    scheduler = build_heavy_scheduler(
        portfolio_every=timedelta(hours=1),
        research_every=timedelta(hours=12),
        entry_backtest_every=timedelta(hours=24),
    )
    names = tuple(job.name for job in scheduler.jobs)

    assert names == ("portfolio", "research", "entry-backtest")
    assert scheduler.jobs[-1].every == timedelta(hours=24)
