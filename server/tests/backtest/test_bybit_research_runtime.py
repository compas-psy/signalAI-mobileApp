from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.backtest.bybit_research_runtime import _eligible_instruments, _select_next
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


def _snapshot(symbol: str, *, at: datetime, marker: str) -> DatasetSnapshot:
    return DatasetSnapshot(
        dataset_name=f"bybit:{symbol}:multistream",
        dataset_version="bybit-multistream-v1",
        schema_version="1",
        snapshot_id=marker * 64,
        tradable_at=at,
        source_watermark={"readiness": "DATA_READY"},
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
