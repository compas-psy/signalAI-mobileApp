from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID


NOW = datetime(2026, 8, 19, 14, 30, tzinfo=UTC)
BYBIT_INTENT_ID = UUID("11111111-2222-3333-4444-555555555555")
TINVEST_INTENT_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


class BybitTransport:
    def __init__(self, replies: dict[tuple[str, str], list[dict] | dict] | None = None):
        self.replies = replies or {}
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def _reply(self, method: str, path: str):
        value = self.replies.get((method, path), {"retCode": 0, "result": {}})
        if isinstance(value, list):
            if not value:
                raise AssertionError(f"no reply left for {method} {path}")
            return value.pop(0)
        return value

    def get(self, path: str, query: dict[str, str]):
        self.calls.append(("GET", path, dict(query)))
        return self._reply("GET", path)

    def post(self, path: str, body: dict[str, object]):
        self.calls.append(("POST", path, dict(body)))
        return self._reply("POST", path)


class TInvestTransport:
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


def _bybit_intent():
    return SimpleNamespace(
        id=BYBIT_INTENT_ID,
        planned_quantity=Decimal("0.5"),
        planned_entry_price=Decimal("100"),
        planned_stop_price=Decimal("90"),
        instrument_id="CRYPTO:BTCUSDT",
    )


def _bybit_plan():
    from app.execution.venues.bybit import BybitOrderPlan

    return BybitOrderPlan(
        symbol="BTCUSDT",
        long=True,
        quantity=Decimal("0.5"),
        entry=Decimal("100"),
        stop_loss=Decimal("90"),
        take_profit=Decimal("120"),
    )


def _tinvest_intent():
    return SimpleNamespace(
        id=TINVEST_INTENT_ID,
        planned_quantity=Decimal("2"),
        planned_entry_price=Decimal("123456.7"),
        planned_stop_price=Decimal("122000"),
        instrument_id="FORTS:SiU6",
    )


def _tinvest_plan():
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


def test_bybit_reconciles_full_position_stop_from_live_position_snapshot():
    from app.execution.venues.bybit import BybitAdapter

    transport = BybitTransport(
        {
            ("GET", "/v5/position/list"): {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "positionIdx": 0,
                            "symbol": "BTCUSDT",
                            "side": "Buy",
                            "size": "0.25",
                            "stopLoss": "90",
                        }
                    ]
                },
            }
        }
    )
    adapter = BybitAdapter(
        transport=transport,
        plan_resolver=lambda _: _bybit_plan(),
        clock=lambda: NOW,
    )
    order = SimpleNamespace(provider_order_id="ENTRY-1", client_order_id="e-1")
    protection = SimpleNamespace(
        provider_order_id="bybit-position-stop:BTCUSDT",
        quantity=Decimal("0.25"),
        stop_price=Decimal("90"),
    )

    result = adapter.reconcile_protection(_bybit_intent(), order, protection)

    assert result.outcome == "MATCHED"
    assert result.quantity == Decimal("0.25")
    assert result.stop_price == Decimal("90")
    assert result.provider_order_id == "bybit-position-stop:BTCUSDT"
    assert transport.calls == [
        ("GET", "/v5/position/list", {"category": "linear", "symbol": "BTCUSDT"})
    ]


def test_bybit_missing_stop_is_not_mistaken_for_protected_position():
    from app.execution.venues.bybit import BybitAdapter

    transport = BybitTransport(
        {
            ("GET", "/v5/position/list"): {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "positionIdx": 0,
                            "symbol": "BTCUSDT",
                            "side": "Buy",
                            "size": "0.25",
                            "stopLoss": "0",
                        }
                    ]
                },
            }
        }
    )
    adapter = BybitAdapter(
        transport=transport,
        plan_resolver=lambda _: _bybit_plan(),
        clock=lambda: NOW,
    )
    protection = SimpleNamespace(
        provider_order_id="bybit-position-stop:BTCUSDT",
        quantity=Decimal("0.25"),
        stop_price=Decimal("90"),
    )

    result = adapter.reconcile_protection(
        _bybit_intent(),
        SimpleNamespace(provider_order_id="ENTRY-1", client_order_id="e-1"),
        protection,
    )

    assert result.outcome == "MISSING"
    assert "stop" in (result.reason or "").lower()


def test_bybit_emergency_flatten_is_market_reduce_only_with_stable_identity():
    from app.execution.venues.bybit import BybitAdapter

    transport = BybitTransport(
        {
            ("POST", "/v5/order/create"): {
                "retCode": 0,
                "result": {"orderId": "CLOSE-1"},
            }
        }
    )
    adapter = BybitAdapter(
        transport=transport,
        plan_resolver=lambda _: _bybit_plan(),
        clock=lambda: NOW,
    )

    ack = adapter.emergency_flatten(
        _bybit_intent(),
        SimpleNamespace(provider_order_id="ENTRY-1", client_order_id="e-1"),
        filled_quantity=Decimal("0.25"),
        client_order_id="x-stable-close",
    )

    assert ack.provider_order_id == "CLOSE-1"
    assert transport.calls == [
        (
            "POST",
            "/v5/order/create",
            {
                "category": "linear",
                "symbol": "BTCUSDT",
                "side": "Sell",
                "orderType": "Market",
                "qty": "0.25",
                "positionIdx": 0,
                "reduceOnly": True,
                "closeOnTrigger": True,
                "orderLinkId": "x-stable-close",
            },
        )
    ]


def test_bybit_emergency_reconciliation_normalizes_filled_status():
    from app.execution.venues.bybit import BybitAdapter

    transport = BybitTransport(
        {
            ("GET", "/v5/order/realtime"): {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "orderId": "CLOSE-1",
                            "orderStatus": "Filled",
                            "updatedTime": "1700000000123",
                        }
                    ]
                },
            }
        }
    )
    adapter = BybitAdapter(
        transport=transport,
        plan_resolver=lambda _: _bybit_plan(),
        clock=lambda: NOW,
    )
    order = SimpleNamespace(client_order_id="x-stable-close", provider_order_id="CLOSE-1")

    result = adapter.reconcile_emergency_flatten(_bybit_intent(), order)

    assert result.outcome == "FOUND"
    assert result.status == "FILLED"
    assert transport.calls[0][2] == {
        "category": "linear",
        "orderLinkId": "x-stable-close",
    }


def test_tinvest_partial_fill_stop_uses_actual_lots_not_planned_lots():
    from app.execution.venues.tinvest import TInvestAdapter

    transport = TInvestTransport(
        {("SandboxService", "PostSandboxStopOrder"): {"stopOrderId": "STOP-1"}}
    )
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _tinvest_plan(),
        clock=lambda: NOW,
        sandbox=True,
    )

    adapter.arm_protection(
        _tinvest_intent(),
        SimpleNamespace(provider_order_id="ENTRY-1", client_order_id="e-1"),
        filled_quantity=Decimal("1"),
    )

    assert transport.calls[0][2]["quantity"] == "1"


def test_tinvest_reconciles_exact_active_stop_by_provider_id_quantity_and_price():
    from app.execution.venues.tinvest import TInvestAdapter

    transport = TInvestTransport(
        {
            ("SandboxService", "GetSandboxStopOrders"): {
                "stopOrders": [
                    {
                        "stopOrderId": "STOP-1",
                        "instrumentUid": "FUT-UID-1",
                        "lotsRequested": "1",
                        "direction": "STOP_ORDER_DIRECTION_SELL",
                        "orderType": "STOP_ORDER_TYPE_STOP_LOSS",
                        "stopPrice": {"units": "122000", "nano": 0},
                    }
                ]
            }
        }
    )
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _tinvest_plan(),
        clock=lambda: NOW,
        sandbox=True,
    )
    protection = SimpleNamespace(
        provider_order_id="STOP-1",
        quantity=Decimal("1"),
        stop_price=Decimal("122000"),
    )

    result = adapter.reconcile_protection(
        _tinvest_intent(),
        SimpleNamespace(provider_order_id="ENTRY-1", client_order_id="e-1"),
        protection,
    )

    assert result.outcome == "MATCHED"
    assert result.quantity == Decimal("1")
    assert result.stop_price == Decimal("122000")
    assert transport.calls == [
        (
            "SandboxService",
            "GetSandboxStopOrders",
            {"accountId": "ACC-1", "status": "STOP_ORDER_STATUS_ACTIVE"},
        )
    ]


def test_tinvest_emergency_flatten_is_opposite_market_order_for_actual_filled_lots():
    from app.execution.venues.tinvest import TInvestAdapter, provider_request_id

    transport = TInvestTransport(
        {("SandboxService", "PostSandboxOrder"): {"orderId": "CLOSE-1"}}
    )
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _tinvest_plan(),
        clock=lambda: NOW,
        sandbox=True,
    )

    ack = adapter.emergency_flatten(
        _tinvest_intent(),
        SimpleNamespace(provider_order_id="ENTRY-1", client_order_id="e-1"),
        filled_quantity=Decimal("1"),
        client_order_id="x-internal-close",
    )

    assert ack.provider_order_id == "CLOSE-1"
    assert transport.calls == [
        (
            "SandboxService",
            "PostSandboxOrder",
            {
                "accountId": "ACC-1",
                "instrumentId": "FUT-UID-1",
                "quantity": "1",
                "direction": "ORDER_DIRECTION_SELL",
                "orderType": "ORDER_TYPE_MARKET",
                "orderId": provider_request_id(_tinvest_intent(), purpose="emergency-flatten"),
                "priceType": "PRICE_TYPE_POINT",
                "confirmMarginTrade": True,
            },
        )
    ]


def test_tinvest_emergency_reconciliation_uses_request_uid_and_normalizes_fill():
    from app.execution.venues.tinvest import TInvestAdapter, provider_request_id

    transport = TInvestTransport(
        {
            ("SandboxService", "GetSandboxOrderState"): {
                "orderId": "CLOSE-1",
                "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
                "orderDate": "2026-08-19T14:30:01Z",
            }
        }
    )
    adapter = TInvestAdapter(
        transport=transport,
        plan_resolver=lambda _: _tinvest_plan(),
        clock=lambda: NOW,
        sandbox=True,
    )

    result = adapter.reconcile_emergency_flatten(
        _tinvest_intent(),
        SimpleNamespace(client_order_id="x-internal-close", provider_order_id=None),
    )

    assert result.outcome == "FOUND"
    assert result.status == "FILLED"
    assert transport.calls[0][2] == {
        "accountId": "ACC-1",
        "orderId": provider_request_id(_tinvest_intent(), purpose="emergency-flatten"),
        "orderIdType": "ORDER_ID_TYPE_REQUEST",
    }
