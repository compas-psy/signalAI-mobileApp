from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.backtest.venue_datasets import (
    BYBIT_DATASET_NAME,
    FORTS_DATASET_NAME,
    REQUIRED_BYBIT_STREAMS,
    REQUIRED_FORTS_STREAMS,
    assess_snapshot_readiness,
    load_venue_readiness,
)
from app.models import DatasetSnapshot


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
START = datetime(2023, 8, 1, 0, 0, tzinfo=UTC)


def _stream(name: str, *, start: datetime = START, end: datetime = NOW - timedelta(hours=1)) -> dict:
    return {
        "name": name,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "rows": 100,
        "content_sha256": (name.encode().hex() + "0" * 64)[:64],
        "artifact_key": f"venue-components/{name}.json",
    }


def _snapshot(*, venue: str, dataset_name: str, streams: tuple[str, ...], extra: dict | None = None) -> DatasetSnapshot:
    watermark = {
        "venue": venue,
        "period_from": START.isoformat(),
        "period_to": (NOW - timedelta(hours=1)).isoformat(),
        "streams": {name: _stream(name) for name in streams},
        **(extra or {}),
    }
    return DatasetSnapshot(
        dataset_name=dataset_name,
        dataset_version="venue-entry-v1",
        schema_version="1",
        snapshot_id=(venue.lower() + "0" * 64)[:64],
        tradable_at=NOW - timedelta(hours=1),
        source_watermark=watermark,
        row_count=len(streams),
        content_sha256="1" * 64,
        manifest_sha256="2" * 64,
        artifact_key=f"{dataset_name}/manifest.json",
    )


def test_missing_dataset_fails_closed() -> None:
    readiness = assess_snapshot_readiness(
        None,
        venue="BYBIT",
        now=NOW,
        required_months=36,
    )

    assert readiness.ready is False
    assert readiness.status == "MISSING_DATASET"
    assert readiness.required_months == 36
    assert "DATASET_MISSING" in readiness.blockers


def test_bybit_requires_every_point_in_time_derivatives_stream() -> None:
    incomplete = tuple(name for name in REQUIRED_BYBIT_STREAMS if name != "funding")
    snapshot = _snapshot(
        venue="BYBIT",
        dataset_name=BYBIT_DATASET_NAME,
        streams=incomplete,
    )

    readiness = assess_snapshot_readiness(
        snapshot,
        venue="BYBIT",
        now=NOW,
        required_months=36,
    )

    assert readiness.ready is False
    assert readiness.status == "INCOMPLETE_DATASET"
    assert "STREAM_MISSING:funding" in readiness.blockers


def test_short_stream_blocks_even_if_manifest_period_claims_36_months() -> None:
    snapshot = _snapshot(
        venue="BYBIT",
        dataset_name=BYBIT_DATASET_NAME,
        streams=REQUIRED_BYBIT_STREAMS,
    )
    watermark = dict(snapshot.source_watermark)
    streams = dict(watermark["streams"])
    streams["open_interest"] = _stream(
        "open_interest",
        start=datetime(2026, 1, 1, tzinfo=UTC),
    )
    watermark["streams"] = streams
    snapshot.source_watermark = watermark

    readiness = assess_snapshot_readiness(
        snapshot,
        venue="BYBIT",
        now=NOW,
        required_months=36,
    )

    assert readiness.ready is False
    assert "STREAM_HISTORY_TOO_SHORT:open_interest" in readiness.blockers


def test_forts_requires_valid_roll_boundaries() -> None:
    snapshot = _snapshot(
        venue="FORTS",
        dataset_name=FORTS_DATASET_NAME,
        streams=REQUIRED_FORTS_STREAMS,
        extra={"roll_boundaries_valid": False, "segment_count": 14},
    )

    readiness = assess_snapshot_readiness(
        snapshot,
        venue="FORTS",
        now=NOW,
        required_months=36,
    )

    assert readiness.ready is False
    assert "ROLL_BOUNDARIES_INVALID" in readiness.blockers


def test_complete_dataset_is_ready() -> None:
    bybit = _snapshot(
        venue="BYBIT",
        dataset_name=BYBIT_DATASET_NAME,
        streams=REQUIRED_BYBIT_STREAMS,
    )
    forts = _snapshot(
        venue="FORTS",
        dataset_name=FORTS_DATASET_NAME,
        streams=REQUIRED_FORTS_STREAMS,
        extra={"roll_boundaries_valid": True, "segment_count": 14},
    )

    assert assess_snapshot_readiness(bybit, venue="BYBIT", now=NOW, required_months=36).ready
    assert assess_snapshot_readiness(forts, venue="FORTS", now=NOW, required_months=36).ready


def test_loader_uses_latest_matching_immutable_snapshot_not_live_bars(session) -> None:
    old = _snapshot(
        venue="BYBIT",
        dataset_name=BYBIT_DATASET_NAME,
        streams=tuple(name for name in REQUIRED_BYBIT_STREAMS if name != "funding"),
    )
    old.snapshot_id = "a" * 64
    old.tradable_at = NOW - timedelta(days=2)
    latest = _snapshot(
        venue="BYBIT",
        dataset_name=BYBIT_DATASET_NAME,
        streams=REQUIRED_BYBIT_STREAMS,
    )
    latest.snapshot_id = "b" * 64
    session.add_all([old, latest])
    session.flush()

    readiness = load_venue_readiness(session, "BYBIT", now=NOW, required_months=36)

    assert readiness.ready is True
    assert readiness.snapshot_id == "b" * 64
