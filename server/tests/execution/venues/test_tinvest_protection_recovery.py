from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID


NOW = datetime(2026, 8, 19, 14, 30, tzinfo=UTC)
INTENT_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


class RecordingTransport:
    def __init__(self, reply: dict):
        self.reply = reply
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def call(self, service: str, method: str, body: dict[str, object]):
        self.calls.append((service, method, dict(body)))
        return self.reply


class SequencedTransport:
    def __init__(self, replies: list[dict]):
        self.replies = list(replies)
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def call(self, service: str, method: str, body: dict[str, object]):
        self.calls.append((service, method, dict(body)))
        if not self.replies:
            raise AssertionError(f"unexpected provider call: {service}.{method}")
        return self.replies.pop(0)


def _intent():
    return SimpleNamespace(
        id=INTENT_ID,
        planned_quantity=Decimal("2"),
        planned_entry_price=Decimal("123456.7"),
        planned_stop_price=Decimal("122000"),
        instrument_id="FORTS:SiU6",
    )


def _plan():
    from app.execution.venues.tinvest import TInvestOrderPlan

    return TInvestOrderPlan(
        account_id="ACC-1",
        instrument_uid="FUT-UID-1",
        ticker="SiU6",
        long=True,
        quantity_lots=2,
        entry=Decimal("123456.7"),
        price_step=Decimal("1"),
        stop_loss=Decimal("122000"),
    )


def _stop(stop_order_id: str, *, lots: str = "1"):
    return {
        "stopOrderId": stop_order_id,
        "instrumentUid": "FUT-UID-1",
        "lotsRequested": lots,
        "direction": "STOP_ORDER_DIRECTION_SELL",
        "orderType": "STOP_ORDER_TYPE_STOP_LOSS",
        "stopPrice": {"units": "122000", "nano": 0},
    }


def test_lost_stop_ack_adopts_the_single_exact_active_stop():
    from app.execution.venues.tinvest import TInvestAdapter

    transport = RecordingTransport({"stopOrders": [_stop("STOP-RECOVERED")]})
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(),
        clock=lambda: NOW,
        sandbox=True,
    )
    protection = SimpleNamespace(
        provider_order_id=None,
        quantity=Decimal("1"),
        stop_price=Decimal("122000"),
    )

    result = adapter.reconcile_protection(
        _intent(),
        SimpleNamespace(provider_order_id="ENTRY-1", client_order_id="e-1"),
        protection,
    )

    assert result.outcome == "MATCHED"
    assert result.provider_order_id == "STOP-RECOVERED"
    assert result.quantity == Decimal("1")
    assert result.stop_price == Decimal("122000")


def test_multiple_exact_active_stops_after_lost_ack_stay_unknown():
    from app.execution.venues.tinvest import TInvestAdapter

    transport = RecordingTransport(
        {"stopOrders": [_stop("STOP-A"), _stop("STOP-B")]}
    )
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(),
        clock=lambda: NOW,
        sandbox=True,
    )
    protection = SimpleNamespace(
        provider_order_id=None,
        quantity=Decimal("1"),
        stop_price=Decimal("122000"),
    )

    result = adapter.reconcile_protection(
        _intent(),
        SimpleNamespace(provider_order_id="ENTRY-1", client_order_id="e-1"),
        protection,
    )

    assert result.outcome == "UNKNOWN"
    assert "exceeds" in (result.reason or "").lower()


def test_incremental_stop_legs_reconcile_as_exact_aggregate_coverage():
    from app.execution.venues.tinvest import TInvestAdapter

    transport = RecordingTransport(
        {"stopOrders": [_stop("STOP-A"), _stop("STOP-B")]}
    )
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(),
        clock=lambda: NOW,
        sandbox=True,
    )
    protection = SimpleNamespace(
        provider_order_id="STOP-B",
        quantity=Decimal("2"),
        stop_price=Decimal("122000"),
    )

    result = adapter.reconcile_protection(
        _intent(),
        SimpleNamespace(provider_order_id="ENTRY-1", client_order_id="e-1"),
        protection,
    )

    assert result.outcome == "MATCHED"
    assert result.provider_order_id == "STOP-B"
    assert result.quantity == Decimal("2")
    assert result.stop_price == Decimal("122000")


def test_expansion_arms_only_uncovered_delta_without_cancelling_existing_stop():
    from app.execution.venues.tinvest import TInvestAdapter, provider_request_id

    transport = SequencedTransport(
        [
            {"stopOrders": [_stop("STOP-A")]},
            {"stopOrderId": "STOP-B"},
        ]
    )
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(),
        clock=lambda: NOW,
        sandbox=True,
    )

    ack = adapter.arm_protection(
        _intent(),
        SimpleNamespace(provider_order_id="ENTRY-1", client_order_id="e-1"),
        filled_quantity=Decimal("2"),
    )

    assert ack.provider_order_id == "STOP-B"
    assert [method for _, method, _ in transport.calls] == [
        "GetSandboxStopOrders",
        "PostSandboxStopOrder",
    ]
    post_body = transport.calls[1][2]
    assert post_body["quantity"] == "1"
    assert post_body["orderId"] == provider_request_id(
        _intent(), purpose="stop-total-2"
    )
