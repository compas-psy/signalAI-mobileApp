"""Production scheduler lane partitioning.

The generic Scheduler remains sequential inside each process. We split jobs
that have no ordering dependency on the latency-critical market path so long
portfolio/research/backfill/optimizer work cannot delay ingest or scan ticks.
"""

from __future__ import annotations

from enum import StrEnum


class SchedulerLane(StrEnum):
    ALL = "all"
    MARKET = "market"
    HEAVY = "heavy"


HEAVY_JOB_NAMES = frozenset(
    {"portfolio", "research", "bybit_research", "risk_optimizer"}
)


def parse_scheduler_lane(raw: str | None) -> SchedulerLane:
    value = (raw or SchedulerLane.ALL.value).strip().lower()
    try:
        return SchedulerLane(value)
    except ValueError as exc:
        allowed = ", ".join(lane.value for lane in SchedulerLane)
        raise ValueError(
            f"unknown SIGNALAI_SCHEDULER_LANE={value!r}; expected one of {allowed}"
        ) from exc


def select_job_names(names, lane: SchedulerLane) -> tuple[str, ...]:
    ordered = tuple(names)
    if lane == SchedulerLane.ALL:
        return ordered
    if lane == SchedulerLane.HEAVY:
        return tuple(name for name in ordered if name in HEAVY_JOB_NAMES)
    if lane == SchedulerLane.MARKET:
        return tuple(name for name in ordered if name not in HEAVY_JOB_NAMES)
    raise ValueError(f"unsupported scheduler lane: {lane!r}")


def apply_scheduler_lane(scheduler, lane: SchedulerLane):
    names = tuple(job.name for job in scheduler.jobs)
    selected = set(select_job_names(names, lane))

    if lane == SchedulerLane.HEAVY:
        missing = HEAVY_JOB_NAMES.difference(names)
        if missing:
            raise RuntimeError(
                "heavy scheduler jobs missing: " + ", ".join(sorted(missing))
            )

    scheduler.jobs[:] = [job for job in scheduler.jobs if job.name in selected]
    return scheduler


__all__ = [
    "HEAVY_JOB_NAMES",
    "SchedulerLane",
    "apply_scheduler_lane",
    "parse_scheduler_lane",
    "select_job_names",
]
