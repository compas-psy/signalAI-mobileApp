"""Provider-neutral execution venue adapter contract for SAI-036.

The adapter deliberately mirrors the proven SAI-026/027 ``ExecutionPort``
seam. It defines what provider integrations must implement without choosing a
venue, storing credentials, issuing network calls or enabling LIVE execution.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Iterable, TypeVar

from ...models.execution import ExecutionIntent, ExecutionOrder
from ..service import (
    ExecutionFillSnapshot,
    ExecutionProtectionAck,
    ExecutionSubmitAck,
    PreSubmitReconciliation,
    SubmissionReconciliation,
)
from .capabilities import (
    VenueCapabilities,
    VenueCapability,
    require_capabilities,
)


CORE_EXECUTION_CAPABILITIES = frozenset(
    {
        VenueCapability.LIMIT_ORDER,
        VenueCapability.CLIENT_ORDER_ID,
        VenueCapability.RECONCILIATION_QUERY,
        VenueCapability.MIN_QTY_STEP,
    }
)


class VenueAdapterConfigurationError(ValueError):
    """Raised before provider I/O when an adapter cannot satisfy the core seam."""


class VenueAdapter(ABC):
    """Common provider contract consumed by the durable execution core."""

    venue: str

    @property
    @abstractmethod
    def capabilities(self) -> VenueCapabilities: ...

    @abstractmethod
    def reconcile_before_submit(
        self,
        intent: ExecutionIntent,
    ) -> PreSubmitReconciliation: ...

    @abstractmethod
    def reconcile_submission(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
    ) -> SubmissionReconciliation: ...

    @abstractmethod
    def submit(
        self,
        intent: ExecutionIntent,
        *,
        client_order_id: str,
    ) -> ExecutionSubmitAck: ...

    @abstractmethod
    def consume_fills(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
    ) -> Iterable[ExecutionFillSnapshot]: ...

    @abstractmethod
    def arm_protection(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
        *,
        filled_quantity: Decimal,
    ) -> ExecutionProtectionAck: ...

    @abstractmethod
    def reconcile(self, intent: ExecutionIntent) -> None: ...

    @abstractmethod
    def manage_until_close(self, intent: ExecutionIntent) -> None: ...


_AdapterT = TypeVar("_AdapterT")


def validate_adapter(adapter: _AdapterT) -> _AdapterT:
    """Fail closed on the minimum capabilities needed by the execution core.

    Validation is intentionally structural rather than ``isinstance`` based so
    provider factories/test doubles can be checked before they are wrapped in a
    concrete ``VenueAdapter`` implementation. Provider-specific optional
    features remain explicit capabilities and are gated by the slices that use
    them; they are not made universal here.
    """

    problems: list[str] = []
    venue = getattr(adapter, "venue", None)
    if not isinstance(venue, str) or not venue.strip():
        problems.append("venue must be a non-empty string")

    capabilities = getattr(adapter, "capabilities", None)
    if not isinstance(capabilities, VenueCapabilities):
        problems.append("capabilities must be VenueCapabilities")
    else:
        check = require_capabilities(capabilities, CORE_EXECUTION_CAPABILITIES)
        problems.extend(
            f"missing capability: {capability.value}"
            for capability in check.missing
        )

    if problems:
        raise VenueAdapterConfigurationError("; ".join(problems))
    return adapter


__all__ = [
    "CORE_EXECUTION_CAPABILITIES",
    "VenueAdapter",
    "VenueAdapterConfigurationError",
    "validate_adapter",
]
