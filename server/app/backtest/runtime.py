"""Heavy-lane coordinator for venue-specific entry backtests.

The first invariant is honesty: a configured 36-month walk-forward must never
silently run on a few months of production bars.  This coordinator therefore
checks FORTS and BYBIT independently and blocks the venue until the historical
dataset is deep enough.  It intentionally lives outside the market scheduler
lane, so readiness/backfill/research work cannot delay signal generation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from .readiness import BacktestDataReadiness, load_venue_readiness


VENUES = ("FORTS", "BYBIT")


def venue_readiness(
    session: Session,
    *,
    now: datetime | None = None,
    cfg: EngineConfig | None = None,
) -> tuple[BacktestDataReadiness, ...]:
    moment = now or datetime.now(UTC)
    config = cfg or get_config()
    return tuple(
        load_venue_readiness(session, venue, now=moment, cfg=config)
        for venue in VENUES
    )


def run_entry_backtest_cycle(session: Session) -> str:
    """Heavy scheduler hook.

    Until a venue has the configured historical depth, the only valid action
    is to block the research run and report the exact deficit.  Creating a
    ``BacktestRun`` with partial history would make Control look green without
    the evidence required by the owner-approved walk-forward policy.
    """

    rows = venue_readiness(session)
    parts: list[str] = []
    for item in rows:
        if item.ready:
            # The deterministic engine is already available, but a venue run
            # must be fed by a point-in-time historical dataset.  Readiness is
            # the production gate for that runner; no synthetic zero-result
            # record is persisted here.
            parts.append(f"{item.venue}=READY")
        else:
            parts.append(
                f"{item.venue}={item.status} "
                f"D1 {item.available_d1_months}/{item.required_months}m "
                f"H1 {item.available_h1_months}/{item.required_months}m"
            )
    return "entry backtest: " + "; ".join(parts)


__all__ = ["VENUES", "run_entry_backtest_cycle", "venue_readiness"]
