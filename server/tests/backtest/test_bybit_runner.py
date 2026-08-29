from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.bybit_runner import ReplayGate, run_bybit_backtest
from app.backtest.robustness import PathObservation
from app.backtest.walk_forward import WalkForwardConfig
from app.datasets.snapshots import (
    DatasetRow,
    DatasetSnapshotBuilder,
    FilesystemSnapshotStore,
    publish_snapshot,
)


def _snapshot(session, tmp_path, *, readiness: str = "DATA_READY"):
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = start + timedelta(days=12)
    rows = []
    for index in range(12):
        observed = start + timedelta(days=index)
        rows.append(
            DatasetRow(
                key=f"klines|{observed.isoformat()}",
                tradable_at=observed + timedelta(hours=1),
                values={
                    "stream": "klines",
                    "observed_at": observed,
                    "close": str(100 + index),
                },
            )
        )
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
            "readiness": readiness,
        },
        rows=rows,
    )
    store = FilesystemSnapshotStore(tmp_path)
    publish_snapshot(session, store=store, manifest=manifest)
    session.flush()
    return manifest, store


def _walk_forward() -> WalkForwardConfig:
    return WalkForwardConfig(
        train_span=timedelta(days=4),
        validation_span=timedelta(days=2),
        test_span=timedelta(days=2),
        embargo=timedelta(0),
        step=timedelta(days=2),
    )


def test_runner_replays_exact_snapshot_and_persists_dataset_oos_evidence(
    session, tmp_path, monkeypatch
) -> None:
    manifest, store = _snapshot(session, tmp_path)

    # Historical replay must not be able to fall through to a live REST reader.
    import app.backtest.bybit_history as history

    monkeypatch.setattr(
        history,
        "http_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("live REST used")),
    )

    seen_snapshot_ids: set[str] = set()

    def evaluator(dataset, fold):
        seen_snapshot_ids.add(dataset.snapshot_id)
        result = []
        for index, sample in enumerate(fold.test):
            net = Decimal("0.02") if index % 2 == 0 else Decimal("-0.01")
            result.append(
                PathObservation(
                    at=sample.observed_at,
                    gross_return=net + Decimal("0.001"),
                    net_return=net,
                    turnover=Decimal("1"),
                    mae=Decimal("-0.01"),
                    mfe=Decimal("0.03"),
                    regime="TEST",
                )
            )
        return tuple(result)

    run = run_bybit_backtest(
        session,
        store=store,
        snapshot_id=manifest.snapshot_id,
        strategy_version="momentum_v2",
        evaluator=evaluator,
        walk_forward=_walk_forward(),
        gate=ReplayGate(
            min_trades=2,
            min_profit_factor=Decimal("1.0"),
            min_expectancy_r=Decimal("0"),
            max_top5_contribution=Decimal("1.0"),
        ),
        label_horizon=timedelta(hours=1),
        periods_per_year=Decimal("365"),
    )
    session.flush()

    assert seen_snapshot_ids == {manifest.snapshot_id}
    assert run.strategy == "momentum_v2"
    assert run.trades >= 2
    assert run.report_json["dataset"]["snapshot_id"] == manifest.snapshot_id
    assert run.report_json["dataset"]["content_sha256"] == manifest.content_sha256
    assert run.report_json["dataset"]["manifest_sha256"] == manifest.manifest_sha256
    assert run.report_json["oos"]["folds"] >= 1
    assert run.report_json["oos"]["observations"] == run.trades
    assert run.gate_detail_json["dataset_readiness"] == "DATA_READY"


def test_runner_records_blocked_dataset_without_calling_strategy(session, tmp_path) -> None:
    manifest, store = _snapshot(session, tmp_path, readiness="DATA_BLOCKED")
    calls = 0

    def evaluator(_dataset, _fold):
        nonlocal calls
        calls += 1
        raise AssertionError("blocked data must not reach strategy replay")

    run = run_bybit_backtest(
        session,
        store=store,
        snapshot_id=manifest.snapshot_id,
        strategy_version="breakout_v2",
        evaluator=evaluator,
        walk_forward=_walk_forward(),
        gate=ReplayGate(),
    )
    session.flush()

    assert calls == 0
    assert run.gate_passed is False
    assert run.trades == 0
    assert run.gate_detail_json["reason"] == "DATA_BLOCKED"
    assert run.report_json["dataset"]["snapshot_id"] == manifest.snapshot_id
