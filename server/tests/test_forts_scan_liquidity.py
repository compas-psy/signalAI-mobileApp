from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.models.enums import AssetClass
from app.pipeline.scan import _liquidity_inputs


def test_forts_scan_reuses_universe_liquidity_when_last_daily_value_is_missing():
    instrument = SimpleNamespace(
        asset_class=AssetClass.FUTURES,
        metadata_json={
            "spread_snapshot": "0.0008",
            "admission": {
                "daily_notional_rub": "650000000",
                "turnover_source": "fresh_board_snapshot",
                "relative_spread_snapshot": "0.0008",
            },
        },
    )
    context = [SimpleNamespace(volume_notional=None)]

    spread, turnover = _liquidity_inputs(instrument, context)

    assert spread == 0.0008
    assert turnover == Decimal("650000000")


def test_forts_scan_prefers_audited_30d_median_to_one_daily_candle():
    instrument = SimpleNamespace(
        asset_class=AssetClass.FUTURES,
        metadata_json={
            "admission": {
                "median_daily_notional_rub": "900000000",
                "turnover_source": "history_30d_median",
                "relative_spread_snapshot": "0.0004",
            },
        },
    )
    context = [SimpleNamespace(volume_notional=Decimal("1000000"))]

    spread, turnover = _liquidity_inputs(instrument, context)

    assert spread == 0.0004
    assert turnover == Decimal("900000000")


def test_non_futures_keep_previous_scan_liquidity_behavior():
    instrument = SimpleNamespace(
        asset_class=AssetClass.CRYPTO_PERPETUAL,
        metadata_json={
            "admission": {"median_daily_notional_rub": "999999999999"},
        },
    )
    context = [SimpleNamespace(volume_notional=Decimal("123456"))]

    spread, turnover = _liquidity_inputs(instrument, context)

    assert spread == 0.0005
    assert turnover == Decimal("123456")
