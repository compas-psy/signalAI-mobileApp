"""Bounded production lifecycle for immutable Bybit research snapshots.

The hot market scanner must never perform a 36-month REST backfill.  This
runtime is therefore scheduled only on the heavy lane and refreshes exactly one
currently tradable crypto-perpetual symbol per invocation. Missing symbols are
backfilled first; afterwards the oldest snapshot is refreshed round-robin.

Both DATA_READY and DATA_BLOCKED manifests are persisted. A blocked manifest is
evidence explaining why replay/optimization cannot proceed, not a reason to
hide the failed coverage check.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from ..datasets.snapshots import FilesystemSnapshotStore
from ..models import DatasetSnapshot, Instrument
from ..models.enums import AssetClass, Venue
from .bybit_dataset import collect_multistream, publish_multistream_snapshot

_DEFAULT_ROOT = "/var/lib/signalai-datasets"


def _eligible_instruments(session: Session) -> tuple[Instrument, ...]:
    return tuple(
        session.execute(
            select(Instrument)
            .where(
                Instrument.venue == Venue.CRYPTO,
                Instrument.asset_class == AssetClass.CRYPTO_PERPETUAL,
                Instrument.in_universe.is_(True),
                Instrument.is_tradable.is_(True),
            )
            .order_by(Instrument.symbol)
        ).scalars()
    )


def _latest_snapshot(session: Session, symbol: str) -> DatasetSnapshot | None:
    return session.execute(
        select(DatasetSnapshot)
        .where(DatasetSnapshot.dataset_name == f"bybit:{symbol}:multistream")
        .order_by(
            DatasetSnapshot.tradable_at.desc(),
            DatasetSnapshot.created_at.desc(),
        )
        .limit(1)
    ).scalars().first()


def _select_next(
    session: Session,
    instruments: tuple[Instrument, ...],
) -> Instrument | None:
    """Choose missing history first, otherwise the oldest refreshed symbol."""
    if not instruments:
        return None

    dated: list[tuple[datetime, str, Instrument]] = []
    for instrument in instruments:
        latest = _latest_snapshot(session, instrument.symbol)
        if latest is None:
            return instrument
        stamp = latest.tradable_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        dated.append((stamp, instrument.symbol, instrument))
    dated.sort(key=lambda item: (item[0], item[1]))
    return dated[0][2]


def refresh_next_bybit_dataset(
    session: Session,
    *,
    now: datetime | None = None,
    cfg: EngineConfig | None = None,
    store: FilesystemSnapshotStore | None = None,
    fetch=None,
) -> str:
    """Refresh one symbol and persist immutable READY/BLOCKED evidence."""
    if not isinstance(session, Session):
        raise ValueError("session must be a SQLAlchemy Session")
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    instruments = _eligible_instruments(session)
    instrument = _select_next(session, instruments)
    if instrument is None:
        return "bybit research: eligible crypto instruments absent"

    config = cfg or get_config()
    months = int(config.get("backtest.walk_forward.min_history_months"))
    if months < 1:
        raise ValueError("backtest.walk_forward.min_history_months must be positive")

    # The end boundary is the start of the currently forming H1 candle; rows
    # before it are closed and therefore eligible for point-in-time research.
    end_at = moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    start_at = end_at - relativedelta(months=months)

    snapshot_store = store or FilesystemSnapshotStore(
        Path(os.environ.get("SIGNALAI_DATASET_SNAPSHOT_ROOT", _DEFAULT_ROOT))
    )
    kwargs = {
        "start_at": start_at,
        "end_at": end_at,
        "min_history_months": months,
    }
    if fetch is not None:
        kwargs["fetch"] = fetch
    collected = collect_multistream(instrument.symbol, **kwargs)
    row = publish_multistream_snapshot(
        session,
        store=snapshot_store,
        built=collected.built,
        require_ready=False,
    )
    blockers = ",".join(
        f"{item.stream}:{item.reason}" for item in collected.built.blockers
    )
    suffix = f" blockers={blockers}" if blockers else ""
    return (
        f"bybit research: {instrument.symbol} {collected.status} "
        f"snapshot={row.snapshot_id[:12]} rows={row.row_count}{suffix}"
    )


__all__ = ["refresh_next_bybit_dataset"]
