from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.backtest.bybit_entry_backtest import EntryReplayGate, run_bybit_entry_backtest
from app.backtest.walk_forward import WalkForwardConfig
from app.datasets.snapshots import (
    DatasetRow,
    DatasetSnapshotBuilder,
    FilesystemSnapshotStore,
    publish_snapshot,
)
from app.models import PaperTrade, TradeIdea


def _carry_snapshot(session, tmp_path):
    start = datetime(2026, 6, 1, tzinfo=UTC)
    end = start + timedelta(days=40)
    rows: list[DatasetRow] = []

    hours = int((end - start).total_seconds() // 3600)
    for index in range(hours):
        observed = start + timedelta(hours=index)
        tradable = observed + timedelta(hours=1)
        rows.extend(
            (
                DatasetRow(
                    key=f"mark_price|{observed.isoformat()}",
                    tradable_at=tradable,
                    values={
                        "stream": "mark_price",
                        "observed_at": observed,
                        "open": Decimal("100"),
                        "high": Decimal("100"),
                        "low": Decimal("100"),
                        "close": Decimal("100"),
                    },
                ),
                DatasetRow(
                    key=f"index_price|{observed.isoformat()}",
                    tradable_at=tradable,
                    values={
                        "stream": "index_price",
                        "observed_at": observed,
                        "open": Decimal("100"),
                        "high": Decimal("100"),
                        "low": Decimal("100"),
                        "close": Decimal("100"),
                    },
                ),
            )
        )

    funding_at = start
    while funding_at < end:
        rows.append(
            DatasetRow(
                key=f"funding|{funding_at.isoformat()}",
                tradable_at=funding_at,
                values={
                    "stream": "funding",
                    "observed_at": funding_at,
                    "funding_rate": Decimal("0.001"),
                },
            )
        )
        funding_at += timedelta(hours=8)

    manifest = DatasetSnapshotBuilder.build(
        dataset_name="bybit:BTCUSDT:multistream",
        dataset_version="bybit-multistream-v1",
        schema_version="1",
        tradable_at=end,
        source_watermark={
            "provider": "bybit-v5-public",
            "symbol": "BTCUSDT",
            "period_start": start,
            "period_end": end,
            "readiness": "DATA_READY",
            "coverage": ["funding", "mark_price", "index_price"],
        },
        rows=rows,
    )
    store = FilesystemSnapshotStore(tmp_path)
    publish_snapshot(session, store=store, manifest=manifest)
    session.flush()
    return manifest, store


def _walk_forward() -> WalkForwardConfig:
    return WalkForwardConfig(
        train_span=timedelta(days=10),
        validation_span=timedelta(days=5),
        test_span=timedelta(days=5),
        embargo=timedelta(0),
        step=timedelta(days=5),
    )


def _gate() -> EntryReplayGate:
    return EntryReplayGate(
        min_trades=3,
        min_profit_factor=Decimal("1"),
        # This threshold belongs to the directional R metric space only.  A
        # successful carry replay proves it cannot leak into the CARRY_BPS gate.
        min_expectancy_r=Decimal("999"),
        max_top5_contribution=Decimal("1"),
    )


def test_crypto_carry_replays_realized_hedged_outcome_in_bps(session, tmp_path) -> None:
    manifest, store = _carry_snapshot(session, tmp_path)
    ideas_before = session.scalar(select(func.count()).select_from(TradeIdea))
    paper_before = session.scalar(select(func.count()).select_from(PaperTrade))

    run = run_bybit_entry_backtest(
        session,
        store=store,
        snapshot_id=manifest.snapshot_id,
        strategy_version="crypto_carry_v1",
        walk_forward=_walk_forward(),
        gate=_gate(),
    )
    session.flush()

    assert run.label.startswith("bybit-carry-backtest-v1:crypto_carry_v1:")
    assert run.strategy == "crypto_carry_v1"
    assert run.trades >= 3
    assert run.gate_passed is True
    assert run.expectancy_r is None
    assert run.net_return is None
    assert run.report_json["metric_space"] == "CARRY_BPS"
    assert run.report_json["outcome_metric"] == "hedged_realized_carry_bps_v1"
    assert Decimal(run.report_json["oos"]["expectancy_bps"]) > 0
    assert run.gate_detail_json["r_threshold_not_applicable"] is True
    assert run.report_json["dataset"]["snapshot_id"] == manifest.snapshot_id
    assert run.report_json["dataset"]["content_sha256"] == manifest.content_sha256
    assert run.report_json["cost_model"]["funding_uncertainty_applied_to_realized_outcome"] is False
    assert session.scalar(select(func.count()).select_from(TradeIdea)) == ideas_before
    assert session.scalar(select(func.count()).select_from(PaperTrade)) == paper_before
