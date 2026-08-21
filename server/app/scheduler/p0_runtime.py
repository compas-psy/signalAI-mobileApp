"""P0 production scheduler hardening installed by package import.

Keeps the tested generic Scheduler intact while replacing/adding runtime jobs:
- scan wake-up is keyed by FORTS/crypto closed-bar watermarks;
- Shadow candidate measurement has its own closed-bar watermark and never calls
  the owner scan/lifecycle path;
- Paper A/B consumes immutable Shadow facts and writes only its isolated
  counterfactual measurement journal;
- owner capital refreshes server-side from read-only broker credentials.
"""

from __future__ import annotations

import os
from datetime import timedelta

from ..capital.runtime import refresh as refresh_capital
from ..pipeline import scan as scan_module
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


def _replace_scan_job(scheduler) -> None:
    previous: dict = {}

    def lane_safe_scan(session) -> str:
        nonlocal previous
        current = snapshot(session)
        if not current:
            return "закрытых H1 FORTS/crypto нет — сканировать нечего"
        changed = changed_lanes(previous, current)
        if previous and not changed:
            detail = ", ".join(
                f"{lane}={stamp.isoformat()}#{count}"
                for lane, (stamp, count) in sorted(current.items())
            )
            return f"новых баров нет по контурам ({detail}) — скан пропущен"

        # Resolve the scan function at execution time. Scheduler bootstrap
        # installs the configured-owner-equity/risk runtime *after* this
        # package is imported; capturing scan() here would silently bypass
        # that safety wrapper and restore the old 100k fallback.
        result = scan_module.scan(session)
        # Advance only after a successful scan. If scan raises, Scheduler
        # rolls the DB transaction back and the same market state is retried.
        previous = dict(current)
        lanes = changed or tuple(sorted(current))
        return (
            f"контуры {','.join(lanes)}; просмотрено {result.scanned}, "
            f"идей {result.produced}, пропущено {len(result.skipped)}, "
            f"отказов {len(result.rejections)}"
        )

    for job in scheduler.jobs:
        if job.name == "scan":
            job.run = lane_safe_scan
            return
    raise RuntimeError("default scheduler has no scan job")


def _add_shadow_job(scheduler, *, every: timedelta, shadow_runner=None) -> None:
    """Insert isolated candidate measurement after ingest and before owner scan.

    The production path is data-driven, not clock-driven: it advances only when
    a FORTS/crypto closed-bar lane changes.  ``evaluated_at`` is pinned to the
    newest visible closed H1 timestamp, so a scheduler/process restart over the
    same market snapshot reproduces the same observation identity instead of
    manufacturing another OOS denominator.

    ``shadow_runner`` is an explicit test seam.  When supplied it is used
    verbatim and is still placed as its own scheduler job; it cannot call the
    owner scan unless the caller deliberately gives it such a function.
    """

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
            scheduler.jobs.insert(index, job)
            return
    raise RuntimeError("default scheduler has no scan job")


def _add_paper_ab_job(scheduler, *, every: timedelta, paper_ab_runner=None) -> None:
    """Run counterfactual Paper measurement after Shadow and before owner scan.

    Unlike Shadow, this job must run on cadence even if a market watermark did
    not change: a previously emitted decision can mature and become resolvable.
    Seeding/outcome writes are idempotent and append-only, so cadence cannot
    manufacture extra opportunities or rewrite old PnL.
    """

    if not isinstance(every, timedelta) or every <= timedelta(0):
        raise ValueError("paper_ab_every must be a positive timedelta")

    if paper_ab_runner is not None:
        run = paper_ab_runner
    else:

        def run(session) -> str:
            from ..experiments.paper_ab_runtime_v1 import run_paper_ab_cycle

            return run_paper_ab_cycle(session).summary()

    scheduler.add("paper_ab", every, run)
    job = scheduler.jobs.pop()
    # The canonical placement is immediately after Shadow.  If a custom
    # scheduler somehow omits Shadow, fail closed rather than silently placing
    # Paper after the owner scan/lifecycle.
    for index, existing in enumerate(scheduler.jobs):
        if existing.name == "shadow":
            scheduler.jobs.insert(index + 1, job)
            return
    raise RuntimeError("default scheduler has no shadow job")


def _add_capital_job(scheduler) -> None:
    scheduler.add(
        "capital",
        _minutes_from_env("SIGNALAI_CAPITAL_EVERY_MINUTES", 5),
        lambda session: refresh_capital(session),
    )
    job = scheduler.jobs.pop()
    # Keep private broker reads before heavy market ingestion so Today gets a
    # fresh owner snapshot quickly even when public history refresh takes long.
    insert_at = 1 if scheduler.jobs else 0
    scheduler.jobs.insert(insert_at, job)


def build_default_scheduler(*args, **kwargs):
    # Candidate measurement belongs to this production hardening layer rather
    # than the generic Scheduler API.  Pop its arguments before delegating to
    # the original builder so existing callers stay source-compatible.
    shadow_every = kwargs.pop("shadow_every", timedelta(minutes=15))
    shadow_runner = kwargs.pop("shadow_runner", None)
    paper_ab_every = kwargs.pop("paper_ab_every", timedelta(minutes=15))
    paper_ab_runner = kwargs.pop("paper_ab_runner", None)

    scheduler = _ORIGINAL_BUILD(*args, **kwargs)
    _replace_scan_job(scheduler)
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
