from datetime import timedelta

from app.scheduler.lanes import SchedulerLane, apply_scheduler_lane
from app.scheduler.runner import build_default_scheduler


def test_heavy_scheduler_order_matches_dependency_chain() -> None:
    scheduler = build_default_scheduler(
        portfolio_every=timedelta(minutes=60),
        research_every=timedelta(minutes=60),
        bybit_research_every=timedelta(minutes=60),
        bybit_backtest_every=timedelta(minutes=60),
        risk_optimizer_every=timedelta(minutes=60),
    )

    apply_scheduler_lane(scheduler, SchedulerLane.HEAVY)

    assert tuple(job.name for job in scheduler.jobs) == (
        "bybit_research",
        "bybit_backtest",
        "risk_optimizer",
        "portfolio",
        "research",
    )
