from __future__ import annotations

from decimal import Decimal

from app.execution.canary_profile_v1 import (
    CANARY_V1_CHALLENGE_TTL_SECONDS,
    validate_canary_v1_payload,
)


def _payload() -> dict[str, object]:
    return {
        "venue": "LIGHTER",
        "environment": "mainnet",
        "instrument_allowlist": ["CRYPTO:PERP:BTCUSDT"],
        "capital_amount": "100",
        "capital_currency": "USDC",
        "hard_caps": {
            "max_order_notional": "10",
            "max_instrument_notional": "25",
            "max_gross_notional": "25",
            "max_open_positions": 1,
            "max_entry_orders": 1,
            "max_leverage": "1",
            "daily_loss_limit": "3",
            "total_loss_limit": "7",
            "max_order_count": 20,
            "max_trade_count": 6,
        },
    }


def test_owner_approved_canary_v1_profile_is_exact_and_uses_five_minute_step_up() -> None:
    assert CANARY_V1_CHALLENGE_TTL_SECONDS == 300
    assert validate_canary_v1_payload(_payload()) == ()


def test_canary_v1_fails_closed_on_any_risk_expansion_or_wrong_collateral() -> None:
    cases = [
        ("capital_amount", "101"),
        ("capital_currency", "USDT"),
    ]
    for field, value in cases:
        payload = _payload()
        payload[field] = value
        assert validate_canary_v1_payload(payload)

    cap_cases = {
        "max_order_notional": "10.01",
        "max_instrument_notional": "25.01",
        "max_gross_notional": "25.01",
        "max_open_positions": 2,
        "max_entry_orders": 2,
        "max_leverage": "1.01",
        "daily_loss_limit": "3.01",
        "total_loss_limit": "7.01",
        "max_order_count": 21,
        "max_trade_count": 7,
    }
    for cap, value in cap_cases.items():
        payload = _payload()
        payload["hard_caps"] = dict(payload["hard_caps"])
        payload["hard_caps"][cap] = value
        assert validate_canary_v1_payload(payload)


def test_canary_v1_rejects_scope_expansion() -> None:
    payload = _payload()
    payload["instrument_allowlist"] = ["CRYPTO:PERP:BTCUSDT", "CRYPTO:PERP:ETHUSDT"]
    blockers = validate_canary_v1_payload(payload)
    assert "CANARY_V1_INSTRUMENT_SCOPE_MISMATCH" in blockers

    payload = _payload()
    payload["venue"] = "OTHER"
    assert "CANARY_V1_VENUE_MISMATCH" in validate_canary_v1_payload(payload)
