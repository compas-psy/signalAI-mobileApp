from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.market.candles import Candle
from app.models import Instrument
from app.models.enums import AssetClass, DerivativesFlow, Venue
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


def _derivatives_bars() -> list[Candle]:
    start = datetime(2026, 8, 20, tzinfo=UTC)
    bars: list[Candle] = []
    price = Decimal("100")
    oi = Decimal("1000")
    for index in range(30):
        price_factor = Decimal("1.001") if index < 29 else Decimal("1.03")
        oi_factor = Decimal("1.001") if index < 29 else Decimal("1.04")
        next_price = price * price_factor
        next_oi = oi * oi_factor
        bars.append(
            Candle(
                open_time=start + timedelta(hours=index),
                open=price,
                high=next_price * Decimal("1.001"),
                low=price * Decimal("0.999"),
                close=next_price,
                open_interest=next_oi,
                source="bybit",
            )
        )
        price = next_price
        oi = next_oi
    return bars


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


def test_bybit_scan_derivatives_flow_uses_measured_price_and_oi_changes() -> None:
    assert hasattr(scan_module, "_derivatives_flow")

    flow, measured = scan_module._derivatives_flow(_derivatives_bars())

    assert measured is True
    assert flow is DerivativesFlow.LONG_BUILDUP
