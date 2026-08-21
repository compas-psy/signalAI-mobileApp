"""Server-only Lighter credential boundary for SAI-068.

This module only loads and normalizes encrypted API-key material for later R5
workers.  It deliberately contains no provider SDK, token generation, signing,
sequence management or trading behavior.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ...integration_secrets import load_secret

LIGHTER_READ_SLOT = "lighter_read"
LIGHTER_TESTNET_TRADE_SLOT = "lighter_testnet_trade"
LIGHTER_TRADE_SLOT = "lighter_trade"

_SLOT_CONTEXT = {
    LIGHTER_READ_SLOT: ("live", "read"),
    LIGHTER_TESTNET_TRADE_SLOT: ("testnet", "trade"),
    LIGHTER_TRADE_SLOT: ("live", "trade"),
}


class LighterCredentialError(ValueError):
    """Raised when stored Lighter credential material is invalid."""


@dataclass(frozen=True, slots=True, repr=False)
class LighterServerCredentials:
    account_index: int
    api_key_index: int
    api_private_key: str
    environment: str
    purpose: str

    def __repr__(self) -> str:
        return (
            "LighterServerCredentials("
            f"account_index={self.account_index}, "
            f"api_key_index={self.api_key_index}, "
            "api_private_key=<redacted>, "
            f"environment={self.environment!r}, "
            f"purpose={self.purpose!r})"
        )


def _parse_index(field: str, raw: str, *, maximum: int | None = None) -> int:
    if not raw.isascii() or not raw.isdigit():
        raise LighterCredentialError(
            f"{field} must be an integer represented with ASCII digits"
        )
    value = int(raw)
    if maximum is not None and value > maximum:
        raise LighterCredentialError(f"{field} must be in range 0..{maximum}")
    return value


def _normalize_private_key(raw: str) -> str:
    value = raw[2:] if raw.lower().startswith("0x") else raw
    if not value or len(value) % 2 != 0 or re.fullmatch(r"[0-9a-fA-F]+", value) is None:
        raise LighterCredentialError("api_private_key must be non-empty hex-encoded bytes")
    return value


def load_lighter_server_credentials(
    db: Session,
    slot: str,
) -> LighterServerCredentials | None:
    context = _SLOT_CONTEXT.get(slot)
    if context is None:
        raise LighterCredentialError(f"unknown Lighter credential slot: {slot!r}")

    raw = load_secret(db, slot)
    if raw is None:
        return None

    required = {"account_index", "api_key_index", "api_private_key"}
    if set(raw) != required:
        raise LighterCredentialError("stored Lighter credential fields do not match contract")

    environment, purpose = context
    return LighterServerCredentials(
        account_index=_parse_index("account_index", raw["account_index"]),
        api_key_index=_parse_index("api_key_index", raw["api_key_index"], maximum=253),
        api_private_key=_normalize_private_key(raw["api_private_key"]),
        environment=environment,
        purpose=purpose,
    )


__all__ = [
    "LIGHTER_READ_SLOT",
    "LIGHTER_TESTNET_TRADE_SLOT",
    "LIGHTER_TRADE_SLOT",
    "LighterCredentialError",
    "LighterServerCredentials",
    "load_lighter_server_credentials",
]
