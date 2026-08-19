"""Provider-neutral venue capabilities for SAI-036.

Capabilities are explicit and fail closed. A venue adapter must opt into every
provider feature it actually supports; absence of evidence is never interpreted
as support. This module contains no provider I/O and does not enable execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class VenueCapability(StrEnum):
    MARKET_ORDER = "market_order"
    LIMIT_ORDER = "limit_order"
    POST_ONLY = "post_only"
    REDUCE_ONLY = "reduce_only"
    STOP_PROTECTION = "stop_protection"
    CANCEL_REPLACE = "cancel_replace"
    CLIENT_ORDER_ID = "client_order_id"
    WEBSOCKET_ACCOUNT_EVENTS = "websocket_account_events"
    FUNDING = "funding"
    LEVERAGE_TIERS = "leverage_tiers"
    MIN_QTY_STEP = "min_qty_step"
    SANDBOX = "sandbox"
    RECONCILIATION_QUERY = "reconciliation_query"


@dataclass(frozen=True)
class VenueCapabilities:
    market_order: bool = False
    limit_order: bool = False
    post_only: bool = False
    reduce_only: bool = False
    stop_protection: bool = False
    cancel_replace: bool = False
    client_order_id: bool = False
    websocket_account_events: bool = False
    funding: bool = False
    leverage_tiers: bool = False
    min_qty_step: bool = False
    sandbox: bool = False
    reconciliation_query: bool = False

    def supports(self, capability: VenueCapability) -> bool:
        """Return explicit support for one capability.

        Enum values intentionally equal dataclass field names so capability
        checks stay declarative and providers cannot silently alias semantics.
        """

        return bool(getattr(self, VenueCapability(capability).value))


@dataclass(frozen=True)
class VenueCapabilityCheck:
    allowed: bool
    missing: tuple[VenueCapability, ...]


def require_capabilities(
    capabilities: VenueCapabilities,
    required: Iterable[VenueCapability],
) -> VenueCapabilityCheck:
    """Check an exact required set before any provider-side action.

    Missing items are sorted by stable enum value for deterministic API/test
    output and audit readability.
    """

    normalized = {VenueCapability(item) for item in required}
    missing = tuple(
        sorted(
            (item for item in normalized if not capabilities.supports(item)),
            key=lambda item: item.value,
        )
    )
    return VenueCapabilityCheck(allowed=not missing, missing=missing)


__all__ = [
    "VenueCapabilities",
    "VenueCapability",
    "VenueCapabilityCheck",
    "require_capabilities",
]
