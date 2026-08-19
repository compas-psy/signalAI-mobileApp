from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest


NOW = datetime(2026, 8, 19, 14, 30, tzinfo=UTC)
INTENT_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


class RecordingTransport:
    def __init__(self, replies: dict[tuple[str, str], list[dict] | dict] | None = None):
        self.replies = replies or {}
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def call(self, service: str, method: str, body: dict[str, object]):
        self.calls.append((service, method, dict(body)))
        value = self.replies.get((service, method), {})
        if isinstance(value, list):
            if not value:
                raise AssertionError(f"no reply left for {service}.{method}")
            return value.pop(0)
        return value


def _intent():
    return SimpleNamespace(
        id=INTENT_ID,
        planned_quantity=Decimal("2"),
        planned_entry_price=Decimal("123456.7"),
        planned_stop_price=Decimal("122000"),
        instrument_id="FORTS:SiU6",
    )


def _plan(*, long: bool = True, stop_entry: bool = False):
    from app.execution.venues.tinvest import TInvestOrderPlan

    return TInvestOrderPlan(
        account_id="ACC-1",
        instrument_uid="FUT-UID-1",
        ticker="SiU6",
        long=long,
        quantity_lots=2,
        entry=Decimal("123456.7"),
        price_step=Decimal("1"),
        stop_loss=Decimal("122000" if long else "125000"),
        stop_entry=stop_entry,
    )


def test_provider_request_ids_are_stable_uid36_and_entry_stop_are_distinct():
    from app.execution.venues.tinvest import provider_request_id

    entry_a = provider_request_id(_intent(), purpose="entry")
    entry_b = provider_request_id(_intent(), purpose="entry")
    stop = provider_request_id(_intent(), purpose="stop")

    assert entry_a == entry_b
    assert len(entry_a) == 36
    assert UUID(entry_a)
    assert UUID(stop)
    assert stop != entry_a


def test_decimal_quotation_and_price_alignment_preserve_legacy_economics_without_float():
    from app.execution.venues.tinvest import align_price, decimal_to_quotation, quotation_to_decimal

    assert align_price(Decimal("123.45"), Decimal("0.1")) == Decimal("123.5")
    assert decimal_to_quotation(Decimal("123.5")) == {"units": "123", "nano": 500000000}
    assert decimal_to_quotation(Decimal("-0.1")) == {"units": "0", "nano": -100000000}
    assert quotation_to_decimal({"units": "123", "nano": 500000000}) == Decimal("123.5")


def test_adapter_declares_only_server_implemented_tinvest_capabilities():
    from app.execution.venues.tinvest import TInvestAdapter

    adapter = TInvestAdapter(
        transport=RecordingTransport(),
        plan_resolver=lambda _: _plan(),
        clock=lambda: NOW,
        sandbox=True,
    )

    capabilities = adapter.capabilities
    assert capabilities.limit_order is True
    assert capabilities.client_order_id is True
    assert capabilities.reconciliation_query is True
    assert capabilities.min_qty_step is True
    assert capabilities.sandbox is True
    assert capabilities.stop_protection is True
    assert capabilities.websocket_account_events is False
    assert capabilities.cancel_replace is False
    assert capabilities.market_order is False
    assert capabilities.reduce_only is False


def test_submit_preserves_legacy_sandbox_limit_order_fields_and_uses_provider_uid():
    from app.execution.venues.tinvest import TInvestAdapter, provider_request_id

    transport = RecordingTransport(
        {("SandboxService", "PostSandboxOrder"): {"orderId": "EXCHANGE-ORDER-1"}}
    )
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(long=True),
        clock=lambda: NOW,
        sandbox=True,
    )

    ack = adapter.submit(_intent(), client_order_id=f"e-{INTENT_ID.hex}")

    assert ack.provider_order_id == "EXCHANGE-ORDER-1"
    assert ack.status == "ACKNOWLEDGED"
    assert ack.acknowledged_at == NOW
    assert transport.calls == [
        (
            "SandboxService",
            "PostSandboxOrder",
            {
                "accountId": "ACC-1",
                "instrumentId": "FUT-UID-1",
                "quantity": "2",
                "price": {"units": "123457", "nano": 0},
                "direction": "ORDER_DIRECTION_BUY",
                "orderType": "ORDER_TYPE_LIMIT",
                "orderId": provider_request_id(_intent(), purpose="entry"),
                "timeInForce": "TIME_IN_FORCE_DAY",
                "priceType": "PRICE_TYPE_POINT",
                "confirmMarginTrade": True,
            },
        )
    ]


def test_production_submit_uses_orders_service_without_sandbox_only_fields():
    from app.execution.venues.tinvest import TInvestAdapter

    transport = RecordingTransport(
        {("OrdersService", "PostOrder"): {"orderId": "EXCHANGE-ORDER-2"}}
    )
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(long=False),
        clock=lambda: NOW,
        sandbox=False,
    )

    adapter.submit(_intent(), client_order_id=f"e-{INTENT_ID.hex}")

    service, method, body = transport.calls[0]
    assert (service, method) == ("OrdersService", "PostOrder")
    assert body["direction"] == "ORDER_DIRECTION_SELL"
    assert body["orderType"] == "ORDER_TYPE_LIMIT"
    assert "timeInForce" not in body
    assert "priceType" not in body
    assert "confirmMarginTrade" not in body


def test_stop_entry_is_rejected_before_provider_io_like_legacy_tinvest_path():
    from app.execution.venues.tinvest import TInvestAdapter, TInvestProviderError

    transport = RecordingTransport()
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(stop_entry=True),
        clock=lambda: NOW,
        sandbox=True,
    )

    with pytest.raises(TInvestProviderError, match="stop-entry"):
        adapter.submit(_intent(), client_order_id=f"e-{INTENT_ID.hex}")

    assert transport.calls == []


def test_pre_submit_reconciliation_looks_up_exact_request_uid_and_fails_closed_on_unknown():
    from app.execution.venues.tinvest import (
        TInvestAdapter,
        TInvestProviderError,
        provider_request_id,
    )

    transport = RecordingTransport(
        {
            ("SandboxService", "GetSandboxOrderState"): [
                TInvestProviderError.not_found("order not found"),
                {"orderId": "EXCHANGE-9", "executionReportStatus": "EXECUTION_REPORT_STATUS_NEW"},
            ]
        }
    )
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(),
        clock=lambda: NOW,
        sandbox=True,
    )

    first = adapter.reconcile_before_submit(_intent())
    second = adapter.reconcile_before_submit(_intent())

    assert first.outcome == "ABSENT"
    assert second.outcome == "UNKNOWN"
    assert "already exists" in (second.reason or "")
    assert transport.calls[0][2] == {
        "accountId": "ACC-1",
        "orderId": provider_request_id(_intent(), purpose="entry"),
        "orderIdType": "ORDER_ID_TYPE_REQUEST",
    }


def test_ambiguous_submission_reconciliation_finds_exchange_order_by_request_uid():
    from app.execution.venues.tinvest import TInvestAdapter

    transport = RecordingTransport(
        {
            ("OrdersService", "GetOrderState"): {
                "orderId": "EXCHANGE-77",
                "executionReportStatus": "EXECUTION_REPORT_STATUS_PARTIALLYFILL",
                "orderDate": "2026-08-19T14:29:59Z",
            }
        }
    )
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(),
        clock=lambda: NOW,
        sandbox=False,
    )
    order = SimpleNamespace(client_order_id=f"e-{INTENT_ID.hex}", provider_order_id=None)

    result = adapter.reconcile_submission(_intent(), order)

    assert result.outcome == "FOUND"
    assert result.provider_order_id == "EXCHANGE-77"
    assert result.status == "EXECUTION_REPORT_STATUS_PARTIALLYFILL"
    assert result.acknowledged_at == datetime(2026, 8, 19, 14, 29, 59, tzinfo=UTC)


def test_fill_stages_become_exact_durable_snapshots_without_duplicating_aggregate_fee():
    from app.execution.venues.tinvest import TInvestAdapter

    transport = RecordingTransport(
        {
            ("OrdersService", "GetOrderState"): {
                "orderId": "EXCHANGE-1",
                "currency": "rub",
                "executedCommission": {"units": "12", "nano": 500000000},
                "stages": [
                    {
                        "tradeId": "TRADE-1",
                        "quantity": "1",
                        "price": {"units": "123456", "nano": 500000000},
                        "executionTime": "2026-08-19T14:30:01Z",
                    },
                    {
                        "tradeId": "TRADE-2",
                        "quantity": "1",
                        "price": {"units": "123457", "nano": 0},
                        "executionTime": "2026-08-19T14:30:02Z",
                    },
                ],
            }
        }
    )
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(),
        clock=lambda: NOW,
        sandbox=False,
    )
    order = SimpleNamespace(provider_order_id="EXCHANGE-1", client_order_id=f"e-{INTENT_ID.hex}")

    fills = list(adapter.consume_fills(_intent(), order))

    assert [(fill.provider_fill_id, fill.quantity, fill.price) for fill in fills] == [
        ("TRADE-1", Decimal("1"), Decimal("123456.5")),
        ("TRADE-2", Decimal("1"), Decimal("123457")),
    ]
    assert all(fill.fee_amount == Decimal("0") for fill in fills)
    assert all(fill.fee_currency == "RUB" for fill in fills)


def test_protective_stop_preserves_legacy_direction_and_sandbox_fields():
    from app.execution.venues.tinvest import TInvestAdapter, provider_request_id

    transport = RecordingTransport(
        {("SandboxService", "PostSandboxStopOrder"): {"stopOrderId": "STOP-1"}}
    )
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(long=True),
        clock=lambda: NOW,
        sandbox=True,
    )
    order = SimpleNamespace(provider_order_id="EXCHANGE-1", client_order_id=f"e-{INTENT_ID.hex}")

    protection = adapter.arm_protection(
        _intent(), order, filled_quantity=Decimal("1")
    )

    assert protection.provider_order_id == "STOP-1"
    assert protection.status == "ACTIVE"
    assert protection.armed_at == NOW
    assert transport.calls == [
        (
            "SandboxService",
            "PostSandboxStopOrder",
            {
                "accountId": "ACC-1",
                "instrumentId": "FUT-UID-1",
                "quantity": "2",
                "stopPrice": {"units": "122000", "nano": 0},
                "direction": "STOP_ORDER_DIRECTION_SELL",
                "stopOrderType": "STOP_ORDER_TYPE_STOP_LOSS",
                "expirationType": "STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL",
                "orderId": provider_request_id(_intent(), purpose="stop"),
                "priceType": "PRICE_TYPE_POINT",
                "confirmMarginTrade": True,
            },
        )
    ]


def test_provider_errors_are_structured_and_non_not_found_errors_reconcile_unknown():
    from app.execution.venues.tinvest import TInvestAdapter, TInvestProviderError

    transport = RecordingTransport(
        {
            ("OrdersService", "GetOrderState"): TInvestProviderError(
                code="UNAVAILABLE", message="temporary transport error"
            )
        }
    )
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(),
        clock=lambda: NOW,
        sandbox=False,
    )

    result = adapter.reconcile_before_submit(_intent())

    assert result.outcome == "UNKNOWN"
    assert "UNAVAILABLE" in (result.reason or "")
    assert "temporary transport error" in (result.reason or "")
