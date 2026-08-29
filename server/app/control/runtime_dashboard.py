"""Owner dashboard composition with immutable backtest-data readiness.

The established dashboard aggregation remains untouched.  This thin composition
layer adds one read-only prerequisite snapshot so the API can distinguish
"backtest not run" from "dataset not yet fit to run".
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..backtest.venue_datasets import BacktestVenue, load_venue_readiness
from ..config import EngineConfig
from .dashboard import build_control_dashboard as _build_base_dashboard


def build_control_dashboard(
    session: Session,
    *,
    venue: BacktestVenue,
    window_hours: int = 168,
    now: datetime | None = None,
    cfg: EngineConfig | None = None,
) -> dict:
    snapshot = _build_base_dashboard(
        session,
        venue=venue,
        window_hours=window_hours,
        now=now,
        cfg=cfg,
    )
    snapshot["backtest"]["data_readiness"] = load_venue_readiness(
        session,
        venue,
        now=now,
        cfg=cfg,
    ).as_json()
    return snapshot


__all__ = ["build_control_dashboard"]
