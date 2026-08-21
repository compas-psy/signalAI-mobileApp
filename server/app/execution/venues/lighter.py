"""Transport-free Lighter provider contract for SAI-066.

This module records only provider identity, documented capabilities and network
endpoints.  It deliberately does not implement :class:`VenueAdapter`, import a
Lighter SDK, resolve credentials or perform provider I/O.  Later R5 slices may
build market/account facts and an execution adapter on top of this boundary.

Capability evidence was re-checked against the public Lighter API/SDK docs on
2026-08-21.  Features that are not yet mapped precisely to SignalAI semantics
remain false rather than being inferred from adjacent provider functionality.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .capabilities import VenueCapabilities


class LighterEnvironment(StrEnum):
    MAINNET = "mainnet"
    TESTNET = "testnet"


LIGHTER_CAPABILITIES = VenueCapabilities(
    market_order=True,
    limit_order=True,
    post_only=True,
    reduce_only=True,
    stop_protection=True,
    cancel_replace=True,
    client_order_id=True,
    websocket_account_events=True,
    funding=True,
    # Lighter exposes account/margin configuration, but SAI-066 does not yet
    # prove the exact leverage-tier semantics expected by SignalAI. Fail closed
    # until the account/margin facts slice owns that mapping.
    leverage_tiers=False,
    min_qty_step=True,
    sandbox=True,
    reconciliation_query=True,
)


_REST_URLS = {
    LighterEnvironment.MAINNET: "https://mainnet.zklighter.elliot.ai",
    LighterEnvironment.TESTNET: "https://testnet.zklighter.elliot.ai",
}

_WEBSOCKET_URLS = {
    LighterEnvironment.MAINNET: "wss://mainnet.zklighter.elliot.ai/stream",
    LighterEnvironment.TESTNET: "wss://testnet.zklighter.elliot.ai/stream",
}


def _parse_environment(value: LighterEnvironment | str) -> LighterEnvironment:
    try:
        return LighterEnvironment(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unknown Lighter environment: {value!r}") from exc


def lighter_base_url(environment: LighterEnvironment | str) -> str:
    """Return the explicit REST base URL; unknown environments fail closed."""

    return _REST_URLS[_parse_environment(environment)]


def lighter_websocket_url(environment: LighterEnvironment | str) -> str:
    """Return the explicit WebSocket URL; unknown environments fail closed."""

    return _WEBSOCKET_URLS[_parse_environment(environment)]


@dataclass(frozen=True)
class LighterVenueProfile:
    """Static provider metadata accepted by the provider-neutral core validator."""

    venue: str = "LIGHTER"
    capabilities: VenueCapabilities = LIGHTER_CAPABILITIES
    environments: tuple[LighterEnvironment, ...] = (
        LighterEnvironment.MAINNET,
        LighterEnvironment.TESTNET,
    )


LIGHTER_VENUE_PROFILE = LighterVenueProfile()


__all__ = [
    "LIGHTER_CAPABILITIES",
    "LIGHTER_VENUE_PROFILE",
    "LighterEnvironment",
    "LighterVenueProfile",
    "lighter_base_url",
    "lighter_websocket_url",
]
