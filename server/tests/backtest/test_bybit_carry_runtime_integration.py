from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.backtest.bybit_research_runtime import run_next_bybit_entry_backtests
from app.datasets.snapshots import FilesystemSnapshotStore
from app.models import DatasetSnapshot


def test_ready_snapshot_runs_specialized_carry_before_directional_suite(
    session, tmp_path, monkeypatch
) -> None:
    snapshot = DatasetSnapshot(
        dataset_name="bybit:BTCUSDT:multistream",
        dataset_version="bybit-multistream-v1",
        schema_version="1",
        snapshot_id="a" * 64,
        tradable_at=datetime(2026, 8, 31, 12, tzinfo=UTC),
        source_watermark={"symbol": "BTCUSDT", "readiness": "DATA_READY"},
        row_count=1,
        content_sha256="b" * 64,
        manifest_sha256="c" * 64,
        artifact_key="bybit_BTCUSDT/test.json",
    )
    session.add(snapshot)
    session.flush()

    import app.backtest.bybit_research_runtime as runtime

    calls: list[tuple[str, str]] = []

    def fake_directional(_session, **kwargs):
        calls.append(("directional", kwargs["snapshot_id"]))
        return (
            SimpleNamespace(
                strategy="momentum_v2",
                gate_passed=True,
                trades=212,
                gate_detail_json={},
            ),
        )

    def fake_carry(_session, **kwargs):
        calls.append(("carry", kwargs["snapshot_id"]))
        return SimpleNamespace(
            strategy="crypto_carry_v1",
            gate_passed=True,
            trades=205,
            gate_detail_json={"metric_space": "CARRY_BPS"},
        )

    monkeypatch.setattr(runtime, "run_pending_bybit_entry_backtests", fake_directional)
    monkeypatch.setattr(runtime, "run_pending_bybit_carry_backtest", fake_carry)

    detail = run_next_bybit_entry_backtests(
        session,
        store=FilesystemSnapshotStore(tmp_path),
    )

    assert calls == [
        ("carry", snapshot.snapshot_id),
        ("directional", snapshot.snapshot_id),
    ]
    assert "momentum_v2=PASS/212" in detail
    assert "crypto_carry_v1=PASS/205" in detail
