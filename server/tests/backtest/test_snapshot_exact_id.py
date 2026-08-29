from __future__ import annotations

from datetime import UTC, datetime

from app.datasets.snapshots import (
    DatasetRow,
    DatasetSnapshotBuilder,
    DatasetSnapshotResolver,
    FilesystemSnapshotStore,
    publish_snapshot,
)


def test_resolve_snapshot_id_returns_exact_persisted_identity(session, tmp_path) -> None:
    at = datetime(2026, 8, 29, tzinfo=UTC)
    store = FilesystemSnapshotStore(tmp_path)
    manifest = DatasetSnapshotBuilder.build(
        dataset_name="bybit:BTCUSDT:multistream",
        dataset_version="bybit-multistream-v1",
        schema_version="1",
        tradable_at=at,
        source_watermark={"readiness": "DATA_READY"},
        rows=(DatasetRow("klines|one", at, {"stream": "klines", "close": "100"}),),
    )
    publish_snapshot(session, store=store, manifest=manifest)
    session.flush()

    resolved = DatasetSnapshotResolver(session, store=store).resolve_snapshot_id(
        manifest.snapshot_id
    )

    assert resolved.snapshot_id == manifest.snapshot_id
    assert resolved.content_sha256 == manifest.content_sha256
    assert resolved.source_watermark["readiness"] == "DATA_READY"


def test_resolve_snapshot_id_rejects_unknown_identity(session, tmp_path) -> None:
    store = FilesystemSnapshotStore(tmp_path)
    resolver = DatasetSnapshotResolver(session, store=store)

    try:
        resolver.resolve_snapshot_id("0" * 64)
    except KeyError as exc:
        assert "snapshot" in str(exc).lower()
    else:
        raise AssertionError("unknown snapshot identity must fail closed")
