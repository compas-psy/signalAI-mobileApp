from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.execution.service import (
    ExecutionFillSnapshot,
    ExecutionProtectionAck,
    ExecutionSubmitAck,
    PreSubmitReconciliation,
    SubmissionReconciliation,
)


def test_capabilities_are_explicit_and_fail_closed_by_default():
    from app.execution.venues.capabilities import VenueCapabilities

    capabilities = VenueCapabilities()

    assert capabilities.market_order is False
    assert capabilities.limit_order is False
    assert capabilities.post_only is False
    assert capabilities.reduce_only is False
    assert capabilities.stop_protection is False
    assert capabilities.cancel_replace is False
    assert capabilities.client_order_id is False
    assert capabilities.websocket_account_events is False
    assert capabilities.funding is False
    assert capabilities.leverage_tiers is False
    assert capabilities.min_qty_step is False
    assert capabilities.sandbox is False
    assert capabilities.reconciliation_query is False


def test_required_capabilities_report_exact_missing_set():
    from app.execution.venues.capabilities import (
        VenueCapability,
        VenueCapabilities,
        require_capabilities,
    )

    capabilities = VenueCapabilities(
        limit_order=True,
        client_order_id=True,
        reconciliation_query=True,
    )

    result = require_capabilities(
        capabilities,
        {
            VenueCapability.LIMIT_ORDER,
            VenueCapability.CLIENT_ORDER_ID,
            VenueCapability.RECONCILIATION_QUERY,
            VenueCapability.REDUCE_ONLY,
            VenueCapability.STOP_PROTECTION,
        },
    )

    assert result.allowed is False
    assert result.missing == (
        VenueCapability.REDUCE_ONLY,
        VenueCapability.STOP_PROTECTION,
    )


def test_capability_requirement_is_allowed_only_when_every_item_is_supported():
    from app.execution.venues.capabilities import (
        VenueCapability,
        VenueCapabilities,
        require_capabilities,
    )

    capabilities = VenueCapabilities(
        limit_order=True,
        client_order_id=True,
        reconciliation_query=True,
        min_qty_step=True,
    )

    result = require_capabilities(
        capabilities,
        {
            VenueCapability.LIMIT_ORDER,
            VenueCapability.CLIENT_ORDER_ID,
            VenueCapability.RECONCILIATION_QUERY,
            VenueCapability.MIN_QTY_STEP,
        },
    )

    assert result.allowed is True
    assert result.missing == ()


def test_venue_adapter_contract_matches_existing_execution_port_surface():
    from app.execution.venues.base import VenueAdapter
    from app.execution.venues.capabilities import VenueCapabilities

    class FakeAdapter(VenueAdapter):
        venue = "FAKE"

        @property
        def capabilities(self) -> VenueCapabilities:
            return VenueCapabilities(
                limit_order=True,
                client_order_id=True,
                reconciliation_query=True,
            )

        def reconcile_before_submit(self, intent):
            return PreSubmitReconciliation.absent()

        def reconcile_submission(self, intent, order):
            return SubmissionReconciliation.absent()

        def submit(self, intent, *, client_order_id: str):
            return ExecutionSubmitAck(
                provider_order_id="p-1",
                status="ACKNOWLEDGED",
                acknowledged_at=datetime(2026, 8, 19, tzinfo=UTC),
            )

        def consume_fills(self, intent, order):
            return (
                ExecutionFillSnapshot(
                    provider_fill_id="f-1",
                    quantity=Decimal("1"),
                    price=Decimal("100"),
                    fee_amount=Decimal("0"),
                    fee_currency="USD",
                    filled_at=datetime(2026, 8, 19, tzinfo=UTC),
                ),
            )

        def arm_protection(self, intent, order, *, filled_quantity: Decimal):
            return ExecutionProtectionAck(
                provider_order_id="stop-1",
                status="ACTIVE",
                armed_at=datetime(2026, 8, 19, tzinfo=UTC),
            )

        def reconcile(self, intent) -> None:
            return None

        def manage_until_close(self, intent) -> None:
            return None

    adapter = FakeAdapter()

    assert adapter.venue == "FAKE"
    assert adapter.capabilities.limit_order is True
    assert adapter.capabilities.market_order is False


def test_adapter_validation_rejects_blank_venue_and_missing_core_execution_capabilities():
    from app.execution.venues.base import VenueAdapterConfigurationError, validate_adapter
    from app.execution.venues.capabilities import VenueCapabilities

    class BrokenAdapter:
        venue = "   "
        capabilities = VenueCapabilities()

    with pytest.raises(VenueAdapterConfigurationError) as exc:
        validate_adapter(BrokenAdapter())

    message = str(exc.value)
    assert "venue" in message
    assert "client_order_id" in message
    assert "reconciliation_query" in message


def test_adapter_validation_does_not_require_provider_features_that_are_not_universal():
    from app.execution.venues.base import validate_adapter
    from app.execution.venues.capabilities import VenueCapabilities

    class MinimalSafeAdapter:
        venue = "MINIMAL"
        capabilities = VenueCapabilities(
            limit_order=True,
            client_order_id=True,
            reconciliation_query=True,
            min_qty_step=True,
        )

    validated = validate_adapter(MinimalSafeAdapter())

    assert validated is not None
    assert validated.venue == "MINIMAL"
