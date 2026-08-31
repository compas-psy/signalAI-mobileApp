from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.backtest.bybit_entry_backtest import (
    EntryReplayGate,
    EntrySignal,
    run_bybit_entry_backtest,
    run_pending_bybit_entry_backtests,
)
from app.backtest.walk_forward import WalkForwardConfig
from app.datasets.snapshots import (
    DatasetRow,
    DatasetSnapshotBuilder,
    FilesystemSnapshotStore,
    publish_snapshot,
)
from app.models import PaperTrade, TradeIdea
from app.models.enums import Direction


def _snapshot(session, tmp_path):
    start = datetime(2026, 6, 1, tzinfo=UTC)
    end = start + timedelta(days=40)
    rows = []
    hours = int((end - start).total_seconds() // 3600)
    for index in range(hours):
        observed = start + timedelta(hours=index)
        close = Decimal("100") + Decimal(index) * Decimal("0.1")
        rows.append(
            DatasetRow(
                key=f"klines|{observed.isoformat()}",
                tradable_at=observed + timedelta(hours=1),
                values={
                    "stream": "klines",
                    "observed_at": observed,
                    "open": close - Decimal("0.05"),
                    "high": close + Decimal("1"),
                    "low": close - Decimal("1"),
                    "close": close,
                    "volume_units": Decimal("10"),
                    "volume_notional": close * Decimal("10"),
                    "open_interest": Decimal("1000") + Decimal(index),
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
            "readiness": "DATA_READY",
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


def _fake_signal(*, trigger_bars, evaluated_at, **_kwargs):
    # One signal per UTC day keeps the fixture small while exercising several
    # disjoint OOS folds. Entry is the exact last PIT-visible H1 close.
    if evaluated_at.hour != 1 or not trigger_bars:
        return None
    return EntrySignal(
        direction=Direction.LONG,
        entry_reference=trigger_bars[-1].close,
        horizon=timedelta(hours=24),
        regime="TEST_TREND",
    )


def _gate() -> EntryReplayGate:
    return EntryReplayGate(
        min_trades=3,
        min_profit_factor=Decimal("1"),
        min_expectancy_r=Decimal("0.1"),
        max_top5_contribution=Decimal("1"),
    )


def test_entry_backtest_uses_exact_snapshot_and_existing_directional_alpha_r(
    session, tmp_path, monkeypatch
) -> None:
    manifest, store = _snapshot(session, tmp_path)

    import app.backtest.bybit_history as history

    monkeypatch.setattr(
        history,
        "http_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("live REST used")),
    )
    ideas_before = session.scalar(select(func.count()).select_from(TradeIdea))
    paper_before = session.scalar(select(func.count()).select_from(PaperTrade))

    run = run_bybit_entry_backtest(
        session,
        store=store,
        snapshot_id=manifest.snapshot_id,
        strategy_version="momentum_v2",
        signal_evaluator=_fake_signal,
        walk_forward=_walk_forward(),
        gate=_gate(),
        round_trip_cost_bps=Decimal("0"),
    )
    session.flush()

    assert run.strategy == "momentum_v2"
    assert run.trades >= 3
    assert run.net_return is None
    assert run.expectancy_r is not None and run.expectancy_r > 0
    # A profitable sample with zero losses has mathematically infinite PF.
    # Persist NULL rather than a made-up finite sentinel that can overflow the
    # Numeric column; the immutable report keeps the exact semantic value.
    assert run.profit_factor is None
    assert run.report_json["oos"]["profit_factor"] == "INF"
    assert run.gate_detail_json["criteria"]["min_profit_factor"] is True
    assert run.gate_passed is True
    assert run.report_json["metric_space"] == "R_MULTIPLES"
    assert run.report_json["outcome_metric"] == "paper_directional_alpha_r_v1"
    assert run.report_json["dataset"]["snapshot_id"] == manifest.snapshot_id
    assert run.report_json["dataset"]["content_sha256"] == manifest.content_sha256
    assert run.report_json["oos"]["folds"] >= 1
    assert run.report_json["oos"]["trades"] == run.trades
    assert session.scalar(select(func.count()).select_from(TradeIdea)) == ideas_before
    assert session.scalar(select(func.count()).select_from(PaperTrade)) == paper_before


def test_entry_backtest_blocks_strategies_missing_historical_facts_without_fake_signal(
    session, tmp_path
) -> None:
    manifest, store = _snapshot(session, tmp_path)
    calls = 0

    def should_not_run(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("blocked strategy evaluator must not run")

    run = run_bybit_entry_backtest(
        session,
        store=store,
        snapshot_id=manifest.snapshot_id,
        strategy_version="breakout_v2",
        signal_evaluator=should_not_run,
        walk_forward=_walk_forward(),
        gate=_gate(),
    )
    session.flush()

    assert calls == 0
    assert run.gate_passed is False
    assert run.trades == 0
    assert run.gate_detail_json["reason"] == "HISTORICAL_SPREAD_UNAVAILABLE"
    assert run.report_json["dataset"]["snapshot_id"] == manifest.snapshot_id


def test_pending_suite_is_idempotent_per_strategy_and_snapshot(session, tmp_path) -> None:
    manifest, store = _snapshot(session, tmp_path)

    first = run_pending_bybit_entry_backtests(
        session,
        store=store,
        snapshot_id=manifest.snapshot_id,
        signal_evaluators={"momentum_v2": _fake_signal},
        walk_forward=_walk_forward(),
        gate=_gate(),
        round_trip_cost_bps=Decimal("0"),
    )
    session.flush()
    second = run_pending_bybit_entry_backtests(
        session,
        store=store,
        snapshot_id=manifest.snapshot_id,
        signal_evaluators={"momentum_v2": _fake_signal},
        walk_forward=_walk_forward(),
        gate=_gate(),
        round_trip_cost_bps=Decimal("0"),
    )

    # Carry has its own realized CARRY_BPS outcome contract and is intentionally
    # not forced through the directional-R suite.
    assert {run.strategy for run in first} == {
        "momentum_v2",
        "mean_reversion_v1",
        "breakout_v2",
    }
    assert second == ()
