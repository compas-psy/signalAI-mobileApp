from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.backtest.venue_datasets import BYBIT_DATASET_NAME, REQUIRED_BYBIT_STREAMS
from app.control.runtime_dashboard import build_control_dashboard
from app.models import DatasetSnapshot


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
START = datetime(2023, 8, 1, tzinfo=UTC)


def _ready_bybit_snapshot() -> DatasetSnapshot:
    streams = {
        name: {
            "name": name,
            "from": START.isoformat(),
            "to": (NOW - timedelta(hours=1)).isoformat(),
            "rows": 100,
            "content_sha256": (name.encode().hex() + "0" * 64)[:64],
            "artifact_key": f"venue-components/{name}.json",
        }
        for name in REQUIRED_BYBIT_STREAMS
    }
    return DatasetSnapshot(
        dataset_name=BYBIT_DATASET_NAME,
        dataset_version="venue-entry-v1",
        schema_version="1",
        snapshot_id="c" * 64,
        tradable_at=NOW - timedelta(hours=1),
        source_watermark={
            "venue": "BYBIT",
            "period_from": START.isoformat(),
            "period_to": (NOW - timedelta(hours=1)).isoformat(),
            "streams": streams,
        },
        row_count=len(streams),
        content_sha256="d" * 64,
        manifest_sha256="e" * 64,
        artifact_key=f"{BYBIT_DATASET_NAME}/manifest.json",
    )


def test_control_reports_missing_dataset_instead_of_ambiguous_no_backtest(session) -> None:
    snapshot = build_control_dashboard(session, venue="BYBIT", now=NOW)

    readiness = snapshot["backtest"]["data_readiness"]
    assert readiness["status"] == "MISSING_DATASET"
    assert readiness["ready"] is False
    assert readiness["required_months"] == 36
    assert readiness["snapshot_id"] is None


def test_control_reports_exact_ready_snapshot_identity(session) -> None:
    session.add(_ready_bybit_snapshot())
    session.flush()

    snapshot = build_control_dashboard(session, venue="BYBIT", now=NOW)

    readiness = snapshot["backtest"]["data_readiness"]
    assert readiness["status"] == "READY"
    assert readiness["ready"] is True
    assert readiness["snapshot_id"] == "c" * 64
    assert readiness["required_months"] == 36
