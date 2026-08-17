"""P0 production scheduler hardening installed by package import.

Keeps the tested generic Scheduler intact while replacing two runtime jobs:
- scan wake-up is keyed by FORTS/crypto closed-bar watermarks;
- owner capital refreshes server-side from read-only broker credentials.
"""

from __future__ import annotations

import os
from datetime import timedelta

from ..capital.runtime import refresh as refresh_capital
from ..pipeline import scan as scan_module
from . import runner
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
            return f"новых баров по контурам нет ({detail}) — скан пропущен"

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
    scheduler = _ORIGINAL_BUILD(*args, **kwargs)
    _replace_scan_job(scheduler)
    _add_capital_job(scheduler)
    return scheduler


def install() -> None:
    if runner.build_default_scheduler is not build_default_scheduler:
        runner.build_default_scheduler = build_default_scheduler


__all__ = ["build_default_scheduler", "install"]
