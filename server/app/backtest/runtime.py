"""Heavy-lane coordinator for venue entry backtests.

The coordinator is deliberately independent per venue.  A failed or incomplete
FORTS dataset must not hide BYBIT readiness and vice versa.  Until an immutable
dataset is READY it records no synthetic ``BacktestRun`` and never reports a
passing gate.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from .venue_datasets import BacktestDataReadiness, BacktestVenue, load_venue_readiness

VENUES: tuple[BacktestVenue, ...] = ("FORTS", "BYBIT")


def venue_readiness(
    session: Session,
    *,
    now: datetime | None = None,
    cfg: EngineConfig | None = None,
) -> tuple[BacktestDataReadiness, ...]:
    moment = now or datetime.now(UTC)
    config = cfg or get_config()
    rows: list[BacktestDataReadiness] = []
    for venue in VENUES:
        rows.append(load_venue_readiness(session, venue, now=moment, cfg=config))
    return tuple(rows)


def run_entry_backtest_cycle(
    session: Session,
    *,
    now: datetime | None = None,
    cfg: EngineConfig | None = None,
) -> str:
    """Report immutable prerequisites; dataset refresh/runner hooks attach here.

    This initial coordinator intentionally does *not* create zero-result runs.
    A later collector may make a venue READY, at which point the deterministic
    runner can execute against that exact snapshot identity.
    """

    moment = now or datetime.now(UTC)
    config = cfg or get_config()
    parts: list[str] = []
    for venue in VENUES:
        try:
            item = load_venue_readiness(session, venue, now=moment, cfg=config)
        except Exception as exc:
            parts.append(f"{venue}=ERROR {type(exc).__name__}: {exc}")
            continue
        if item.ready:
            parts.append(
                f"{venue}=READY dataset={item.snapshot_id} history={item.available_months}m"
            )
        else:
            blockers = ",".join(item.blockers[:3]) or "UNKNOWN"
            parts.append(
                f"{venue}={item.status} history={item.available_months}/"
                f"{item.required_months}m blockers={blockers}"
            )
    return "entry backtest: " + "; ".join(parts)


__all__ = ["VENUES", "run_entry_backtest_cycle", "venue_readiness"]
