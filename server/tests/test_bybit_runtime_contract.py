from datetime import UTC, datetime
from decimal import Decimal

from app.market.candles import Candle
from app.models import Instrument
from app.models.enums import AssetClass, Venue
from app.pipeline import scan as scan_module
from app.shadow.collector_v1 import _metadata_facts


def _bybit_instrument(*, metadata: dict) -> Instrument:
    return Instrument(
        instrument_id="CRYPTO:PERP:BTCUSDT",
        venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL,
        symbol="BTCUSDT",
        currency="USDT",
        tick_size=Decimal("0.1"),
        tick_value=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        metadata_json=metadata,
    )


def _candle(*, notional: str) -> Candle:
    return Candle(
        open_time=datetime(2026, 8, 29, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume_notional=Decimal(notional),
        source="bybit",
    )


def test_bybit_scan_reuses_crypto_universe_admission_measurements() -> None:
    instrument = _bybit_instrument(
        metadata={
            "admission": {
                "median_daily_notional_usdt": "30000000",
                "relative_spread": "0.0004",
            }
        }
    )

    spread, turnover = scan_module._liquidity_inputs(
        instrument,
        [_candle(notional="1000000")],
    )

    assert turnover == Decimal("30000000")
    assert spread == 0.0004


def test_bybit_scan_uses_crypto_liquidity_limits() -> None:
    instrument = _bybit_instrument(metadata={})

    assert hasattr(scan_module, "_liquidity_config_paths")
    min_path, spread_path = scan_module._liquidity_config_paths(instrument)

    assert min_path == "universe.crypto.min_median_daily_notional_usdt"
    assert spread_path == "universe.crypto.max_median_relative_spread"


def test_shadow_reads_canonical_crypto_relative_spread() -> None:
    instrument = _bybit_instrument(
        metadata={
            "admission": {
                "median_daily_notional_usdt": "30000000",
                "relative_spread": "0.0004",
            },
            "shadow_cost_model": {
                "round_trip_cost_bps": "8.0",
            },
        }
    )

    facts = _metadata_facts(instrument, datetime(2026, 8, 29, tzinfo=UTC))

    assert facts.spread_bps == Decimal("4.0000")
    assert facts.round_trip_cost_bps == Decimal("8.0")
