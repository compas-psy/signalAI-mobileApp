from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.backtest.bybit_research_runtime import (
    _eligible_instruments,
    _select_next,
    run_next_bybit_entry_backtests,
)
from app.datasets.snapshots import FilesystemSnapshotStore
from app.models import DatasetSnapshot, Instrument
from app.models.enums import AssetClass, Venue


def _instrument(symbol: str) -> Instrument:
    return Instrument(
        instrument_id=f"CRYPTO:PERP:{symbol}",
        venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL,
        symbol=symbol,
        title=symbol,
        currency="USDT",
        tick_size=Decimal("0.1"),
        tick_value=Decimal("0.1"),
        lot_size=1,
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        in_universe=True,
        is_tradable=True,
    )


def _snapshot(
    symbol: str,
    *,
    at: datetime,
    marker: str,
    readiness: str = "DATA_READY",
) -> DatasetSnapshot:
    return DatasetSnapshot(
        dataset_name=f"bybit:{symbol}:multistream",
        dataset_version="bybit-multistream-v1",
        schema_version="1",
        snapshot_id=marker * 64,
        tradable_at=at,
        source_watermark={"symbol": symbol, "readiness": readiness},
        row_count=1,
        content_sha256=("f" if marker != "f" else "e") * 64,
        manifest_sha256=marker * 64,
        artifact_key=f"bybit_{symbol}/{marker * 64}.json",
    )


def test_missing_bybit_symbol_is_backfilled_before_already_snapshotted_symbol(session) -> None:
    btc = _instrument("BTCUSDT")
    eth = _instrument("ETHUSDT")
    session.add_all([btc, eth])
    session.flush()
    session.add(
        _snapshot(
            "BTCUSDT",
            at=datetime(2026, 8, 29, 20, tzinfo=UTC),
            marker="a",
        )
    )
    session.flush()

    instruments = _eligible_instruments(session)
    selected = _select_next(session, instruments)

    assert [item.symbol for item in instruments] == ["BTCUSDT", "ETHUSDT"]
    assert selected is not None
    assert selected.symbol == "ETHUSDT"


def test_oldest_bybit_snapshot_is_refreshed_first_after_initial_backfill(session) -> None:
    btc = _instrument("BTCUSDT")
    eth = _instrument("ETHUSDT")
    session.add_all([btc, eth])
    session.flush()
    session.add_all(
        [
            _snapshot(
                "BTCUSDT",
                at=datetime(2026, 8, 28, 20, tzinfo=UTC),
                marker="b",
            ),
            _snapshot(
                "ETHUSDT",
                at=datetime(2026, 8, 29, 20, tzinfo=UTC),
                marker="c",
            ),
        ]
    )
    session.flush()

    selected = _select_next(session, _eligible_instruments(session))

    assert selected is not None
    assert selected.symbol == "BTCUSDT"


def test_current_blocked_snapshot_shadows_older_ready_snapshot_for_backtest(
    session, tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 8, 30, 8, tzinfo=UTC)
    session.add_all(
        [
            _snapshot("BTCUSDT", at=now - timedelta(days=1), marker="d"),
            _snapshot(
                "BTCUSDT",
                at=now,
                marker="e",
                readiness="DATA_BLOCKED",
            ),
        ]
    )
    session.flush()
    calls: list[str] = []

    import app.backtest.bybit_research_runtime as runtime

    monkeypatch.setattr(
        runtime,
        "run_pending_bybit_entry_backtests",
        lambda *_args, **kwargs: calls.append(kwargs["snapshot_id"]) or (),
    )

    detail = run_next_bybit_entry_backtests(
        session,
        store=FilesystemSnapshotStore(tmp_path),
    )

    assert calls == []
    assert detail == "bybit backtest: no current DATA_READY snapshot pending evidence"


def test_current_ready_snapshot_is_sent_to_idempotent_entry_suite(
    session, tmp_path, monkeypatch
) -> None:
    now = datetime(2026, 8, 30, 8, tzinfo=UTC)
    snapshot = _snapshot("BTCUSDT", at=now, marker="f")
    session.add(snapshot)
    session.flush()
    calls: list[str] = []

    import app.backtest.bybit_research_runtime as runtime

    def fake_suite(_session, **kwargs):
        calls.append(kwargs["snapshot_id"])
        return (
            SimpleNamespace(
                strategy="momentum_v2",
                gate_passed=True,
                trades=238,
                gate_detail_json={},
            ),
            SimpleNamespace(
                strategy="breakout_v2",
                gate_passed=False,
                trades=0,
                gate_detail_json={"reason": "HISTORICAL_SPREAD_UNAVAILABLE"},
            ),
        )

    # This test isolates the generic entry-suite orchestration and intentionally
    # uses a manifest row without an artifact file. Specialized carry has its
    # own integration test with a real/controlled seam, so keep it out here.
    monkeypatch.setattr(runtime, "run_pending_bybit_carry_backtest", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runtime, "run_pending_bybit_entry_backtests", fake_suite)

    detail = run_next_bybit_entry_backtests(
        session,
        store=FilesystemSnapshotStore(tmp_path),
    )

    assert calls == [snapshot.snapshot_id]
    assert "BTCUSDT" in detail
    assert "momentum_v2=PASS/238" in detail
    assert "breakout_v2=HISTORICAL_SPREAD_UNAVAILABLE/0" in detail
