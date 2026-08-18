from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.datasets.snapshots import (
    DatasetRow,
    DatasetSnapshotBuilder,
    DatasetSnapshotResolver,
    FilesystemSnapshotStore,
    SnapshotIntegrityError,
    publish_snapshot,
)
from app.models import DatasetSnapshot


BASE = datetime(2026, 8, 18, 9, tzinfo=UTC)


def rows() -> tuple[DatasetRow, ...]:
    return (
        DatasetRow(
            key="BTCUSDT:1h:2026-08-18T08:00:00Z",
            tradable_at=BASE,
            values={"close": "118000.5", "atr": "850.25"},
        ),
        DatasetRow(
            key="ETHUSDT:1h:2026-08-18T08:00:00Z",
            tradable_at=BASE,
            values={"close": "4520.1", "atr": "42.75"},
        ),
    )


def build(*, snapshot_at: datetime = BASE, dataset_rows=None, watermark="wm-001"):
    return DatasetSnapshotBuilder.build(
        dataset_name="short_horizon_features",
        dataset_version="features_v1",
        schema_version="1",
        tradable_at=snapshot_at,
        source_watermark={"market_bars": watermark},
        rows=tuple(dataset_rows or rows()),
    )


def test_same_logical_snapshot_is_content_addressed_and_deterministic():
    first = build(dataset_rows=rows())
    second = build(dataset_rows=tuple(reversed(rows())))

    assert first.snapshot_id == second.snapshot_id
    assert first.content_sha256 == second.content_sha256
    assert first.manifest_sha256 == second.manifest_sha256
    assert first.row_count == 2


def test_changed_content_creates_a_new_snapshot_identity():
    original = build()
    changed_rows = list(rows())
    changed_rows[0] = DatasetRow(
        key=changed_rows[0].key,
        tradable_at=changed_rows[0].tradable_at,
        values={"close": "118001.5", "atr": "850.25"},
    )
    changed = build(dataset_rows=changed_rows)

    assert changed.snapshot_id != original.snapshot_id
    assert changed.content_sha256 != original.content_sha256


def test_builder_rejects_future_rows_relative_to_snapshot_tradable_at():
    future = DatasetRow(
        key="BTCUSDT:future",
        tradable_at=BASE + timedelta(seconds=1),
        values={"close": "999"},
    )
    with pytest.raises(ValueError, match="future row"):
        build(dataset_rows=(future,))


def test_publish_is_atomic_idempotent_and_manifest_is_append_only(session, tmp_path):
    store = FilesystemSnapshotStore(tmp_path)
    manifest = build()

    first = publish_snapshot(session, store=store, manifest=manifest)
    session.flush()
    second = publish_snapshot(session, store=store, manifest=manifest)
    session.flush()

    assert first.id == second.id
    assert session.execute(select(DatasetSnapshot)).scalars().all() == [first]
    assert store.read_bytes(manifest.artifact_key) == manifest.artifact_bytes

    first.row_count = 999
    with pytest.raises(DBAPIError):
        session.flush()


def test_resolver_is_point_in_time_and_never_uses_future_snapshot(session, tmp_path):
    store = FilesystemSnapshotStore(tmp_path)
    older = build(snapshot_at=BASE, watermark="wm-old")
    newer_rows = tuple(
        DatasetRow(
            key=row.key,
            tradable_at=BASE + timedelta(hours=1),
            values=row.values,
        )
        for row in rows()
    )
    newer = build(
        snapshot_at=BASE + timedelta(hours=1),
        dataset_rows=newer_rows,
        watermark="wm-new",
    )
    publish_snapshot(session, store=store, manifest=older)
    publish_snapshot(session, store=store, manifest=newer)
    session.flush()

    resolver = DatasetSnapshotResolver(session, store=store)
    at_old_time = resolver.resolve(
        "short_horizon_features", decision_time=BASE + timedelta(minutes=30)
    )
    at_new_time = resolver.resolve(
        "short_horizon_features", decision_time=BASE + timedelta(hours=1, minutes=1)
    )

    assert at_old_time.snapshot_id == older.snapshot_id
    assert at_old_time.source_watermark == {"market_bars": "wm-old"}
    assert all(row.tradable_at <= BASE + timedelta(minutes=30) for row in at_old_time.rows)
    assert at_new_time.snapshot_id == newer.snapshot_id


def test_live_and_backtest_paths_share_exact_same_snapshot_resolver(session, tmp_path):
    store = FilesystemSnapshotStore(tmp_path)
    manifest = build()
    publish_snapshot(session, store=store, manifest=manifest)
    session.flush()
    resolver = DatasetSnapshotResolver(session, store=store)
    decision_time = BASE + timedelta(minutes=1)

    live = resolver.resolve_live("short_horizon_features", decision_time=decision_time)
    replay = resolver.resolve_backtest(
        "short_horizon_features", decision_time=decision_time
    )

    assert live == replay
    assert live.snapshot_id == manifest.snapshot_id
    assert live.content_sha256 == manifest.content_sha256
    assert live.manifest_sha256 == manifest.manifest_sha256


def test_replay_output_contains_audit_identity_and_exact_rows(session, tmp_path):
    store = FilesystemSnapshotStore(tmp_path)
    manifest = build()
    publish_snapshot(session, store=store, manifest=manifest)
    session.flush()

    result = DatasetSnapshotResolver(session, store=store).replay(
        "short_horizon_features", decision_time=BASE + timedelta(minutes=1)
    )

    assert result.audit == {
        "dataset_name": "short_horizon_features",
        "dataset_version": "features_v1",
        "schema_version": "1",
        "snapshot_id": manifest.snapshot_id,
        "content_sha256": manifest.content_sha256,
        "manifest_sha256": manifest.manifest_sha256,
        "source_watermark": {"market_bars": "wm-001"},
        "row_count": 2,
        "tradable_at": BASE.isoformat(),
    }
    assert result.rows == rows()


def test_checksum_mismatch_fails_closed_on_replay(session, tmp_path):
    store = FilesystemSnapshotStore(tmp_path)
    manifest = build()
    publish_snapshot(session, store=store, manifest=manifest)
    session.flush()

    artifact_path = tmp_path / manifest.artifact_key
    artifact_path.write_text(json.dumps({"tampered": True}), encoding="utf-8")

    with pytest.raises(SnapshotIntegrityError, match="checksum"):
        DatasetSnapshotResolver(session, store=store).replay(
            "short_horizon_features", decision_time=BASE + timedelta(minutes=1)
        )
