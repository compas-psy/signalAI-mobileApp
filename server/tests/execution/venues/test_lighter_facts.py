from datetime import UTC, datetime
from decimal import Decimal

import pytest


OBSERVED_AT = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def test_market_metadata_is_normalized_without_losing_fee_or_precision_facts() -> None:
    from app.execution.venues.lighter_facts import parse_lighter_market_fact

    fact = parse_lighter_market_fact(
        {
            "symbol": "ETH",
            "market_id": 0,
            "market_type": "perp",
            "status": "active",
            "taker_fee": "0.0028",
            "maker_fee": "0.0004",
            "liquidation_fee": "0.01",
            "min_base_amount": "0.001",
            "min_quote_amount": "5",
            "supported_size_decimals": 4,
            "supported_price_decimals": 2,
            "supported_quote_decimals": 6,
            "order_quote_limit": "1000000",
            "multiplier": "1",
        },
        observed_at=OBSERVED_AT,
    )

    assert fact.market_id == 0
    assert fact.symbol == "ETH"
    assert fact.status == "active"
    assert fact.min_base_amount == Decimal("0.001")
    assert fact.min_quote_amount == Decimal("5")
    assert fact.size_decimals == 4
    assert fact.price_decimals == 2
    assert fact.maker_fee_pct == Decimal("0.0004")
    assert fact.taker_fee_pct == Decimal("0.0028")
    assert fact.observed_at == OBSERVED_AT


def test_account_and_margin_facts_preserve_provider_values_and_positions() -> None:
    from app.execution.venues.lighter_facts import parse_lighter_account_fact

    fact = parse_lighter_account_fact(
        {
            "account_index": 42,
            "account_trading_mode": 1,
            "available_balance": "1234.56",
            "collateral": "2000.00",
            "total_asset_value": "2100.00",
            "cross_asset_value": "2050.00",
            "cross_initial_margin_requirement": "300.00",
            "cross_maintenance_margin_requirement": "150.00",
            "positions": [
                {
                    "market_id": 0,
                    "symbol": "ETH",
                    "initial_margin_fraction": "0.10",
                    "sign": -1,
                    "position": "2.5",
                    "avg_entry_price": "3200",
                    "position_value": "8000",
                    "unrealized_pnl": "-125.5",
                    "realized_pnl": "40",
                    "liquidation_price": "4100",
                    "total_funding_paid_out": "12.25",
                    "margin_mode": 0,
                    "allocated_margin": "800",
                }
            ],
        },
        observed_at=OBSERVED_AT,
    )

    assert fact.account_index == 42
    assert fact.available_balance == Decimal("1234.56")
    assert fact.collateral == Decimal("2000.00")
    assert fact.cross_initial_margin_requirement == Decimal("300.00")
    assert fact.cross_maintenance_margin_requirement == Decimal("150.00")
    assert fact.observed_at == OBSERVED_AT
    assert len(fact.positions) == 1
    position = fact.positions[0]
    assert position.market_id == 0
    assert position.signed_quantity == Decimal("-2.5")
    assert position.initial_margin_fraction == Decimal("0.10")
    assert position.liquidation_price == Decimal("4100")
    assert position.total_funding_paid_out == Decimal("12.25")


def test_zero_position_is_kept_as_provider_margin_configuration_fact() -> None:
    from app.execution.venues.lighter_facts import parse_lighter_account_fact

    fact = parse_lighter_account_fact(
        {
            "account_index": 7,
            "account_trading_mode": 0,
            "available_balance": "100",
            "collateral": "100",
            "total_asset_value": "100",
            "cross_asset_value": "100",
            "cross_initial_margin_requirement": "0",
            "cross_maintenance_margin_requirement": "0",
            "positions": [
                {
                    "market_id": 1,
                    "symbol": "BTC",
                    "initial_margin_fraction": "0.05",
                    "sign": 1,
                    "position": "0",
                    "avg_entry_price": "0",
                    "position_value": "0",
                    "unrealized_pnl": "0",
                    "realized_pnl": "0",
                    "liquidation_price": "0",
                    "margin_mode": 0,
                    "allocated_margin": "0",
                }
            ],
        },
        observed_at=OBSERVED_AT,
    )

    assert len(fact.positions) == 1
    assert fact.positions[0].signed_quantity == Decimal("0")
    assert fact.positions[0].initial_margin_fraction == Decimal("0.05")


def test_fact_parsing_fails_closed_on_spot_or_invalid_critical_numeric_values() -> None:
    from app.execution.venues.lighter_facts import (
        LighterFactsError,
        parse_lighter_market_fact,
    )

    base = {
        "symbol": "ETH",
        "market_id": 0,
        "market_type": "perp",
        "status": "active",
        "taker_fee": "0",
        "maker_fee": "0",
        "liquidation_fee": "0",
        "min_base_amount": "0.001",
        "min_quote_amount": "5",
        "supported_size_decimals": 4,
        "supported_price_decimals": 2,
        "supported_quote_decimals": 6,
        "order_quote_limit": "1000000",
        "multiplier": "1",
    }

    with pytest.raises(LighterFactsError, match="perp"):
        parse_lighter_market_fact({**base, "market_type": "spot"}, observed_at=OBSERVED_AT)

    with pytest.raises(LighterFactsError, match="min_base_amount"):
        parse_lighter_market_fact({**base, "min_base_amount": "garbage"}, observed_at=OBSERVED_AT)


def test_fact_provenance_requires_timezone_aware_observation_time() -> None:
    from app.execution.venues.lighter_facts import (
        LighterFactsError,
        parse_lighter_account_fact,
    )

    payload = {
        "account_index": 1,
        "account_trading_mode": 1,
        "available_balance": "1",
        "collateral": "1",
        "total_asset_value": "1",
        "cross_asset_value": "1",
        "cross_initial_margin_requirement": "0",
        "cross_maintenance_margin_requirement": "0",
        "positions": [],
    }

    with pytest.raises(LighterFactsError, match="timezone-aware"):
        parse_lighter_account_fact(payload, observed_at=datetime(2026, 8, 21, 12, 0))
