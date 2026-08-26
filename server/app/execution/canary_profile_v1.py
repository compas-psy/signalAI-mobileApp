"""Owner-approved static safety envelope for the first Lighter Canary.

This module does not enable LIVE execution. It only validates that an immutable
Canary policy does not exceed the exact owner-approved v1 risk/scope boundary.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Mapping, Any

CANARY_V1_CHALLENGE_TTL_SECONDS = 300
CANARY_V1_CAPITAL_AMOUNT = Decimal("100")
CANARY_V1_CAPITAL_CURRENCY = "USDC"
CANARY_V1_VENUE = "LIGHTER"
CANARY_V1_INSTRUMENTS = ("CRYPTO:PERP:BTCUSDT",)

_DECIMAL_CAPS = {
    "max_order_notional": Decimal("10"),
    "max_instrument_notional": Decimal("25"),
    "max_gross_notional": Decimal("25"),
    "max_leverage": Decimal("1"),
    "daily_loss_limit": Decimal("3"),
    "total_loss_limit": Decimal("7"),
}
_COUNT_CAPS = {
    "max_open_positions": 1,
    "max_entry_orders": 1,
    "max_order_count": 20,
    "max_trade_count": 6,
}


def _decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def validate_canary_v1_payload(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Return fail-closed blocker codes for any scope/risk expansion."""
    blockers: list[str] = []

    if str(payload.get("venue", "")).upper() != CANARY_V1_VENUE:
        blockers.append("CANARY_V1_VENUE_MISMATCH")
    if str(payload.get("environment", "")).lower() != "mainnet":
        blockers.append("CANARY_V1_ENVIRONMENT_MISMATCH")

    instruments = payload.get("instrument_allowlist")
    if not isinstance(instruments, list) or tuple(instruments) != CANARY_V1_INSTRUMENTS:
        blockers.append("CANARY_V1_INSTRUMENT_SCOPE_MISMATCH")

    capital = _decimal(payload.get("capital_amount"))
    if capital != CANARY_V1_CAPITAL_AMOUNT:
        blockers.append("CANARY_V1_CAPITAL_MISMATCH")
    if str(payload.get("capital_currency", "")).upper() != CANARY_V1_CAPITAL_CURRENCY:
        blockers.append("CANARY_V1_COLLATERAL_MISMATCH")

    caps = payload.get("hard_caps")
    if not isinstance(caps, Mapping):
        blockers.append("CANARY_V1_HARD_CAPS_INVALID")
        return tuple(blockers)

    for name, maximum in _DECIMAL_CAPS.items():
        value = _decimal(caps.get(name))
        if value is None or value > maximum:
            blockers.append(f"CANARY_V1_{name.upper()}_EXCEEDED")
    for name, maximum in _COUNT_CAPS.items():
        value = caps.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value > maximum or value <= 0:
            blockers.append(f"CANARY_V1_{name.upper()}_EXCEEDED")

    return tuple(blockers)


__all__ = [
    "CANARY_V1_CHALLENGE_TTL_SECONDS",
    "validate_canary_v1_payload",
]
