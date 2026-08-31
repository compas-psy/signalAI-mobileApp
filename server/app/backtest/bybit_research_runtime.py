"""Bounded production lifecycle for immutable Bybit research evidence.

The hot market scanner must never perform a 36-month REST backfill or an OOS
replay. Both jobs live on the heavy lane. Dataset refresh handles exactly one
currently tradable crypto-perpetual symbol per invocation; the entry-backtest
step then consumes the newest READY snapshots that still lack R4 evidence.

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
from .bybit_carry_runner import run_pending_bybit_carry_backtest
from .bybit_dataset import DATA_READY, collect_multistream, publish_multistream_snapshot
from .bybit_entry_backtest import run_pending_bybit_entry_backtests

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


def _snapshot_store(store: FilesystemSnapshotStore | None) -> FilesystemSnapshotStore:
    return store or FilesystemSnapshotStore(
        Path(os.environ.get("SIGNALAI_DATASET_SNAPSHOT_ROOT", _DEFAULT_ROOT))
    )


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

    snapshot_store = _snapshot_store(store)
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


def _latest_snapshots(session: Session) -> tuple[DatasetSnapshot, ...]:
    """Return only the current snapshot for each Bybit symbol, newest first."""

    rows = session.execute(
        select(DatasetSnapshot)
        .where(DatasetSnapshot.dataset_name.like("bybit:%:multistream"))
        .order_by(
            DatasetSnapshot.tradable_at.desc(),
            DatasetSnapshot.created_at.desc(),
            DatasetSnapshot.id.desc(),
        )
        .limit(300)
    ).scalars()
    latest: dict[str, DatasetSnapshot] = {}
    for row in rows:
        latest.setdefault(row.dataset_name, row)
    return tuple(
        sorted(
            latest.values(),
            key=lambda row: (row.tradable_at, row.dataset_name),
            reverse=True,
        )
    )


def run_next_bybit_entry_backtests(
    session: Session,
    *,
    cfg: EngineConfig | None = None,
    store: FilesystemSnapshotStore | None = None,
) -> str:
    """Backtest one current READY symbol that still lacks R4 evidence.

    Carry runs first because it has a different realized outcome metric
    (CARRY_BPS). It intentionally writes the canonical strategy/snapshot label;
    the generic directional-R suite then sees that identity and skips its old
    fail-closed carry placeholder instead of creating contradictory evidence.

    Completed snapshot/strategy pairs are identified by deterministic
    BacktestRun labels, so a process restart needs no in-memory cursor. A newer
    DATA_BLOCKED snapshot intentionally shadows an older READY one for the same
    symbol; we never backtest stale evidence while current coverage is broken.
    """

    if not isinstance(session, Session):
        raise ValueError("session must be a SQLAlchemy Session")
    config = cfg or get_config()
    snapshot_store = _snapshot_store(store)

    for snapshot in _latest_snapshots(session):
        watermark = snapshot.source_watermark or {}
        if str(watermark.get("readiness") or "") != DATA_READY:
            continue

        created = []
        carry = run_pending_bybit_carry_backtest(
            session,
            store=snapshot_store,
            snapshot_id=snapshot.snapshot_id,
            cfg=config,
        )
        if carry is not None:
            session.flush()
            created.append(carry)

        created.extend(
            run_pending_bybit_entry_backtests(
                session,
                store=snapshot_store,
                snapshot_id=snapshot.snapshot_id,
                cfg=config,
            )
        )
        runs = tuple(created)
        if not runs:
            continue

        symbol = str(watermark.get("symbol") or snapshot.dataset_name.split(":")[1])
        details = []
        for run in runs:
            reason = (run.gate_detail_json or {}).get("reason")
            status = "PASS" if run.gate_passed else (str(reason) if reason else "GATE_FAIL")
            details.append(f"{run.strategy}={status}/{run.trades}")
        return (
            f"bybit backtest: {symbol} snapshot={snapshot.snapshot_id[:12]} "
            + ", ".join(details)
        )

    return "bybit backtest: no current DATA_READY snapshot pending evidence"


__all__ = ["refresh_next_bybit_dataset", "run_next_bybit_entry_backtests"]
