"""P0 production scheduler hardening installed by package import.

Keeps the tested generic Scheduler intact while adding runtime jobs:
- canonical runner scan owns FORTS/crypto closed-bar wake-up;
- Shadow candidate measurement has its own closed-bar watermark and never calls
  the owner scan/lifecycle path;
- Paper A/B consumes immutable Shadow facts and writes only its isolated
  counterfactual measurement journal;
- Bybit historical research refreshes immutable 36m multi-stream snapshots on
  the heavy lane, never inside the latency-critical market loop;
- bounded risk optimization runs after research evidence and keeps its own
  cadence / minimum-evidence promotion gates;
- owner capital refreshes server-side from read-only broker credentials.
"""

from __future__ import annotations

import os
from datetime import timedelta

from ..capital.runtime import refresh as refresh_capital
from . import runner
from .lanes import apply_scheduler_lane, parse_scheduler_lane
from .market_watermark import changed_lanes, snapshot

_ORIGINAL_BUILD = runner.build_default_scheduler


def _minutes_from_env(name: str, default: int) -> timedelta:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return timedelta(minutes=max(1, value))


def _add_shadow_job(scheduler, *, every: timedelta, shadow_runner=None) -> None:
    """Insert isolated candidate measurement immediately after owner scan."""

    if not isinstance(every, timedelta) or every <= timedelta(0):
        raise ValueError("shadow_every must be a positive timedelta")

    if shadow_runner is not None:
        run = shadow_runner
    else:
        previous: dict = {}

        def run(session) -> str:
            nonlocal previous
            current = snapshot(session)
            if not current:
                return "закрытых H1 FORTS/crypto нет — Shadow пропущен"
            changed = changed_lanes(previous, current)
            if previous and not changed:
                detail = ", ".join(
                    f"{lane}={stamp.isoformat()}#{count}"
                    for lane, (stamp, count) in sorted(current.items())
                )
                return f"новых баров нет по контурам ({detail}) — Shadow пропущен"

            from ..shadow.collector_v1 import collect_shadow

            evaluated_at = max(stamp for stamp, _count in current.values())
            report = collect_shadow(session, evaluated_at=evaluated_at)
            previous = dict(current)
            lanes = changed or tuple(sorted(current))
            return f"контуры {','.join(lanes)}; {report.summary()}"

    scheduler.add("shadow", every, run)
    job = scheduler.jobs.pop()
    for index, existing in enumerate(scheduler.jobs):
        if existing.name == "scan":
            scheduler.jobs.insert(index + 1, job)
            return
    raise RuntimeError("default scheduler has no scan job")


def _add_paper_ab_job(scheduler, *, every: timedelta, paper_ab_runner=None) -> None:
    """Run counterfactual Paper measurement immediately after Shadow."""

    if not isinstance(every, timedelta) or every <= timedelta(0):
        raise ValueError("paper_ab_every must be a positive timedelta")

    if paper_ab_runner is not None:
        upstream = paper_ab_runner
    else:

        def upstream(session) -> str:
            from ..experiments.paper_ab_runtime_v1 import run_paper_ab_cycle

            return run_paper_ab_cycle(session).summary()

    def run(session) -> str:
        """Persist fail-closed promotion measurements in the committed worker cycle."""

        from ..execution.promotion_evidence import collect_registered_promotion_evidence

        detail = upstream(session)
        reports = collect_registered_promotion_evidence(session)
        return f"{detail}; promotion evidence scopes={len(reports)}"

    scheduler.add("paper_ab", every, run)
    job = scheduler.jobs.pop()
    for index, existing in enumerate(scheduler.jobs):
        if existing.name == "shadow":
            scheduler.jobs.insert(index + 1, job)
            return
    raise RuntimeError("default scheduler has no shadow job")


def _add_bybit_research_job(
    scheduler,
    *,
    every: timedelta,
    bybit_research_runner=None,
) -> None:
    """Refresh one immutable Bybit research snapshot after Paper evidence."""

    if not isinstance(every, timedelta) or every <= timedelta(0):
        raise ValueError("bybit_research_every must be a positive timedelta")

    if bybit_research_runner is not None:
        run = bybit_research_runner
    else:

        def run(session) -> str:
            from ..backtest.bybit_research_runtime import refresh_next_bybit_dataset

            return refresh_next_bybit_dataset(session)

    scheduler.add("bybit_research", every, run)
    job = scheduler.jobs.pop()
    for index, existing in enumerate(scheduler.jobs):
        if existing.name == "paper_ab":
            scheduler.jobs.insert(index + 1, job)
            return
    raise RuntimeError("default scheduler has no paper_ab job")


def _add_risk_optimizer_job(
    scheduler,
    *,
    every: timedelta,
    risk_optimizer_runner=None,
) -> None:
    """Schedule bounded risk-policy research after historical evidence.

    The scheduler cadence is only a wake-up. ``maybe_optimize`` independently
    enforces its persisted cadence, minimum sample, dataset-readiness and
    promotion criteria, so a restart or frequent tick cannot promote a policy
    without evidence.
    """

    if not isinstance(every, timedelta) or every <= timedelta(0):
        raise ValueError("risk_optimizer_every must be a positive timedelta")

    if risk_optimizer_runner is not None:
        run = risk_optimizer_runner
    else:

        def run(session) -> str:
            from ..risk.optimizer import maybe_optimize

            return maybe_optimize(session) or "risk optimizer: cadence not due"

    scheduler.add("risk_optimizer", every, run)
    job = scheduler.jobs.pop()
    for index, existing in enumerate(scheduler.jobs):
        if existing.name == "bybit_research":
            scheduler.jobs.insert(index + 1, job)
            return
    raise RuntimeError("default scheduler has no bybit_research job")


def _add_capital_job(scheduler) -> None:
    scheduler.add(
        "capital",
        _minutes_from_env("SIGNALAI_CAPITAL_EVERY_MINUTES", 5),
        lambda session: refresh_capital(session),
    )
    job = scheduler.jobs.pop()
    insert_at = 1 if scheduler.jobs else 0
    scheduler.jobs.insert(insert_at, job)


def build_default_scheduler(*args, **kwargs):
    shadow_every = kwargs.pop("shadow_every", timedelta(minutes=15))
    shadow_runner = kwargs.pop("shadow_runner", None)
    paper_ab_every = kwargs.pop("paper_ab_every", timedelta(minutes=15))
    paper_ab_runner = kwargs.pop("paper_ab_runner", None)
    bybit_research_every = kwargs.pop("bybit_research_every", timedelta(hours=1))
    bybit_research_runner = kwargs.pop("bybit_research_runner", None)
    risk_optimizer_every = kwargs.pop("risk_optimizer_every", timedelta(hours=24))
    risk_optimizer_runner = kwargs.pop("risk_optimizer_runner", None)

    scheduler = _ORIGINAL_BUILD(*args, **kwargs)
    _add_shadow_job(
        scheduler,
        every=shadow_every,
        shadow_runner=shadow_runner,
    )
    _add_paper_ab_job(
        scheduler,
        every=paper_ab_every,
        paper_ab_runner=paper_ab_runner,
    )
    _add_bybit_research_job(
        scheduler,
        every=bybit_research_every,
        bybit_research_runner=bybit_research_runner,
    )
    _add_risk_optimizer_job(
        scheduler,
        every=risk_optimizer_every,
        risk_optimizer_runner=risk_optimizer_runner,
    )
    _add_capital_job(scheduler)
    apply_scheduler_lane(
        scheduler,
        parse_scheduler_lane(os.environ.get("SIGNALAI_SCHEDULER_LANE")),
    )
    return scheduler


def install() -> None:
    if runner.build_default_scheduler is not build_default_scheduler:
        runner.build_default_scheduler = build_default_scheduler


__all__ = ["build_default_scheduler", "install"]
