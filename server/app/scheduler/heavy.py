"""Long-running portfolio/research/backtest scheduler lane.

This process deliberately has no market startup reconciliation, notification
bootstrap or paper/execution jobs.  It shares the same code image and database
with the market scheduler, but slow analytical work cannot hold up the next
market-data/scan tick.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from datetime import timedelta

from ..db import get_session_factory
from ..version import ENGINE_VERSION
from .lanes import SchedulerLane, apply_scheduler_lane
from .runner import Scheduler, build_default_scheduler, run_forever

log = logging.getLogger("signalai.scheduler.heavy")


def _minutes(name: str, default: int) -> timedelta:
    raw = os.environ.get(name)
    if not raw:
        return timedelta(minutes=default)
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"{name}={raw!r} — ожидались минуты целым числом") from None
    if value < 1:
        raise SystemExit(f"{name}={value} — интервал меньше минуты бессмыслен")
    return timedelta(minutes=value)


def build_heavy_scheduler(
    *,
    portfolio_every: timedelta = timedelta(minutes=60),
    research_every: timedelta = timedelta(minutes=720),
    entry_backtest_every: timedelta = timedelta(minutes=60),
) -> Scheduler:
    scheduler = build_default_scheduler(
        portfolio_every=portfolio_every,
        research_every=research_every,
    )

    def entry_backtest(session) -> str:
        from ..backtest.runtime import run_entry_backtest_cycle

        return run_entry_backtest_cycle(session)

    scheduler.add("entry-backtest", entry_backtest_every, entry_backtest)
    # Explicit even if compose already sets SIGNALAI_SCHEDULER_LANE=heavy:
    # this entrypoint must fail closed if reused outside compose.
    apply_scheduler_lane(scheduler, SchedulerLane.HEAVY)

    job_names = tuple(job.name for job in scheduler.jobs)
    expected = ("portfolio", "research", "entry-backtest")
    if job_names != expected:
        raise RuntimeError(f"unexpected heavy scheduler jobs: {job_names!r}")
    return scheduler


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("SIGNALAI_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    scheduler = build_heavy_scheduler(
        portfolio_every=_minutes("SIGNALAI_PORTFOLIO_EVERY_MINUTES", 60),
        research_every=_minutes("SIGNALAI_RESEARCH_EVERY_MINUTES", 720),
        entry_backtest_every=_minutes("SIGNALAI_ENTRY_BACKTEST_EVERY_MINUTES", 60),
    )

    stopping = {"now": False}

    def handle(signum, _frame):
        log.info("получен сигнал %s — heavy lane остановится после текущей задачи", signum)
        stopping["now"] = True

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)

    log.info(
        "heavy scheduler %s запущен: %s",
        ENGINE_VERSION,
        ", ".join(
            f"{job.name} каждые {int(job.every.total_seconds() // 60)} мин"
            for job in scheduler.jobs
        ),
    )
    run_forever(
        get_session_factory(),
        scheduler,
        interval_seconds=int(os.environ.get("SIGNALAI_TICK_SECONDS", "60")),
        stop=lambda: stopping["now"],
    )
    log.info("heavy scheduler остановлен")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["build_heavy_scheduler", "main"]
