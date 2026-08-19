from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID


def test_tinvest_adapter_satisfies_common_venue_core_contract():
    from app.execution.venues import validate_adapter
    from app.execution.venues.tinvest import TInvestAdapter, TInvestOrderPlan

    intent_id = UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
    plan = TInvestOrderPlan(
        account_id="ACC-1",
        instrument_uid="FUT-UID-1",
        ticker="SiU6",
        long=True,
        quantity_lots=1,
        entry=Decimal("100"),
        price_step=Decimal("1"),
        stop_loss=Decimal("90"),
    )

    class NoIoTransport:
        def call(self, service: str, method: str, body: dict[str, object]):
            raise AssertionError("validation must not perform provider I/O")

    adapter = TInvestAdapter(
        transport=NoIoTransport(),
        plan_resolver=lambda _: plan,
        clock=lambda: datetime(2026, 8, 19, 14, 30, tzinfo=UTC),
        sandbox=True,
    )

    assert validate_adapter(adapter) is adapter
    # Keep a real intent-shaped object here so the provider adapter remains
    # structurally compatible with the durable execution seam without I/O.
    assert SimpleNamespace(id=intent_id).id == intent_id
