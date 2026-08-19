from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest


NOW = datetime(2026, 8, 19, 13, 30, tzinfo=UTC)
INTENT_ID = UUID("11111111-2222-3333-4444-555555555555")


class RecordingTransport:
    def __init__(self, replies: dict[tuple[str, str], list[dict] | dict] | None = None):
        self.replies = replies or {}
        self.calls: list[tuple[str, str, object]] = []

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


def _intent():
    return SimpleNamespace(
        id=INTENT_ID,
        planned_quantity=Decimal("0.5"),
        planned_entry_price=Decimal("100"),
        planned_stop_price=Decimal("110"),
        instrument_id="CRYPTO:BTCUSDT",
    )


def _plan(*, long: bool = False, stop_entry: bool = False):
    from app.execution.venues.bybit import BybitOrderPlan

    return BybitOrderPlan(
        symbol="BTCUSDT",
        long=long,
        quantity=Decimal("0.5"),
        entry=Decimal("100"),
        stop_loss=Decimal("110" if not long else "90"),
        take_profit=Decimal("80" if not long else "120"),
        stop_entry=stop_entry,
    )


def test_hmac_auth_payload_matches_existing_flutter_v5_contract():
    from app.execution.venues.bybit import BybitV5HmacAuth

    auth = BybitV5HmacAuth(api_key="KEY123", api_secret="SECRET")
    body = (
        '{"category":"linear","symbol":"BTCUSDT","side":"Sell",'
        '"orderType":"Limit","qty":"0.5","price":"100",'
        '"timeInForce":"GTC","stopLoss":"110","takeProfit":"80",'
        '"tpslMode":"Full","positionIdx":0}'
    )

    signed = auth.headers(timestamp_ms=1_700_000_000_000, payload=body)

    expected_payload = f"1700000000000KEY1235000{body}"
    expected_signature = hmac.new(
        b"SECRET",
        expected_payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    assert signed["X-BAPI-API-KEY"] == "KEY123"
    assert signed["X-BAPI-TIMESTAMP"] == "1700000000000"
    assert signed["X-BAPI-RECV-WINDOW"] == "5000"
    assert signed["X-BAPI-SIGN"] == expected_signature


def test_legacy_order_body_preserves_mobile_economic_fields_exactly():
    from app.execution.venues.bybit import legacy_order_body

    body = legacy_order_body(_plan(long=False))

    assert body == {
        "category": "linear",
        "symbol": "BTCUSDT",
        "side": "Sell",
        "orderType": "Limit",
        "qty": "0.5",
        "price": "100",
        "timeInForce": "GTC",
        "stopLoss": "110",
        "takeProfit": "80",
        "tpslMode": "Full",
        "positionIdx": 0,
    }


def test_stop_entry_payload_preserves_trigger_direction_parity():
    from app.execution.venues.bybit import legacy_order_body

    long_body = legacy_order_body(_plan(long=True, stop_entry=True))
    short_body = legacy_order_body(_plan(long=False, stop_entry=True))

    assert long_body["triggerPrice"] == "100"
    assert long_body["triggerDirection"] == 1
    assert long_body["triggerBy"] == "LastPrice"
    assert short_body["triggerDirection"] == 2


def test_adapter_declares_current_bybit_capabilities_without_claiming_ws_or_reduce_only():
    from app.execution.venues.bybit import BybitAdapter

    adapter = BybitAdapter(
        transport=RecordingTransport(),
        plan_resolver=lambda _: _plan(),
        clock=lambda: NOW,
    )

    capabilities = adapter.capabilities
    assert capabilities.limit_order is True
    assert capabilities.client_order_id is True
    assert capabilities.reconciliation_query is True
    assert capabilities.min_qty_step is True
    assert capabilities.sandbox is True
    assert capabilities.stop_protection is True
    assert capabilities.websocket_account_events is False
    assert capabilities.reduce_only is False
    assert capabilities.market_order is False


def test_submit_keeps_legacy_economic_payload_and_adds_stable_order_link_id():
    from app.execution.venues.bybit import BybitAdapter

    transport = RecordingTransport(
        {("POST", "/v5/order/create"): {"retCode": 0, "result": {"orderId": "ORD-1"}}}
    )
    adapter = BybitAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(long=False),
        clock=lambda: NOW,
    )

    ack = adapter.submit(_intent(), client_order_id="e-stable-1")

    assert ack.provider_order_id == "ORD-1"
    assert ack.status == "ACKNOWLEDGED"
    assert ack.acknowledged_at == NOW
    method, path, body = transport.calls.single if False else transport.calls[0]
    assert method == "POST"
    assert path == "/v5/order/create"
    expected = {
        "category": "linear",
        "symbol": "BTCUSDT",
        "side": "Sell",
        "orderType": "Limit",
        "qty": "0.5",
        "price": "100",
        "timeInForce": "GTC",
        "stopLoss": "110",
        "takeProfit": "80",
        "tpslMode": "Full",
        "positionIdx": 0,
    }
    assert {key: body[key] for key in expected} == expected
    assert body["orderLinkId"] == "e-stable-1"


def test_pre_submit_reconciliation_uses_deterministic_order_link_id_and_fails_closed_on_error():
    from app.execution.venues.bybit import BybitAdapter

    transport = RecordingTransport(
        {
            ("GET", "/v5/order/realtime"): [
                {"retCode": 0, "result": {"list": []}},
                {"retCode": 10001, "retMsg": "temporary provider error"},
            ]
        }
    )
    adapter = BybitAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(),
        clock=lambda: NOW,
    )

    first = adapter.reconcile_before_submit(_intent())
    second = adapter.reconcile_before_submit(_intent())

    assert first.outcome == "ABSENT"
    assert transport.calls[0] == (
        "GET",
        "/v5/order/realtime",
        {"category": "linear", "orderLinkId": f"e-{INTENT_ID.hex}"},
    )
    assert second.outcome == "UNKNOWN"
    assert "temporary provider error" in (second.reason or "")


def test_ambiguous_submission_reconciliation_returns_existing_provider_order():
    from app.execution.venues.bybit import BybitAdapter

    transport = RecordingTransport(
        {
            ("GET", "/v5/order/realtime"): {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "orderId": "ORD-9",
                            "orderStatus": "New",
                            "updatedTime": "1700000000123",
                        }
                    ]
                },
            }
        }
    )
    adapter = BybitAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(),
        clock=lambda: NOW,
    )
    order = SimpleNamespace(client_order_id="e-stable-9")

    result = adapter.reconcile_submission(_intent(), order)

    assert result.outcome == "FOUND"
    assert result.provider_order_id == "ORD-9"
    assert result.status == "New"
    assert result.acknowledged_at == datetime.fromtimestamp(1700000000.123, tz=UTC)
    assert transport.calls[0][2] == {
        "category": "linear",
        "orderLinkId": "e-stable-9",
    }


def test_submission_reconciliation_falls_back_to_history_for_terminal_order():
    from app.execution.venues.bybit import BybitAdapter

    transport = RecordingTransport(
        {
            ("GET", "/v5/order/realtime"): {
                "retCode": 0,
                "result": {"list": []},
            },
            ("GET", "/v5/order/history"): {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "orderId": "ORD-CANCELLED-9",
                            "orderStatus": "Cancelled",
                            "updatedTime": "1700000000456",
                        }
                    ]
                },
            },
        }
    )
    adapter = BybitAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(),
        clock=lambda: NOW,
    )
    order = SimpleNamespace(client_order_id="e-stable-cancelled")

    result = adapter.reconcile_submission(_intent(), order)

    assert result.outcome == "FOUND"
    assert result.provider_order_id == "ORD-CANCELLED-9"
    assert result.status == "Cancelled"
    assert result.acknowledged_at == datetime.fromtimestamp(1700000000.456, tz=UTC)
    assert transport.calls == [
        (
            "GET",
            "/v5/order/realtime",
            {"category": "linear", "orderLinkId": "e-stable-cancelled"},
        ),
        (
            "GET",
            "/v5/order/history",
            {"category": "linear", "orderLinkId": "e-stable-cancelled"},
        ),
    ]


def test_emergency_reconciliation_uses_history_before_declaring_close_absent():
    from app.execution.venues.bybit import BybitAdapter

    transport = RecordingTransport(
        {
            ("GET", "/v5/order/realtime"): {
                "retCode": 0,
                "result": {"list": []},
            },
            ("GET", "/v5/order/history"): {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "orderId": "ORD-EMERGENCY-1",
                            "orderStatus": "Filled",
                            "updatedTime": "1700000000789",
                        }
                    ]
                },
            },
        }
    )
    adapter = BybitAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(),
        clock=lambda: NOW,
    )
    order = SimpleNamespace(client_order_id="x-stable-close")

    result = adapter.reconcile_emergency_flatten(_intent(), order)

    assert result.outcome == "FOUND"
    assert result.provider_order_id == "ORD-EMERGENCY-1"
    assert result.status == "FILLED"
    assert result.acknowledged_at == datetime.fromtimestamp(1700000000.789, tz=UTC)
    assert transport.calls == [
        (
            "GET",
            "/v5/order/realtime",
            {"category": "linear", "orderLinkId": "x-stable-close"},
        ),
        (
            "GET",
            "/v5/order/history",
            {"category": "linear", "orderLinkId": "x-stable-close"},
        ),
    ]


def test_fill_parsing_and_position_stop_match_existing_bybit_semantics():
    from app.execution.venues.bybit import BybitAdapter

    transport = RecordingTransport(
        {
            ("GET", "/v5/execution/list"): {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "execId": "F-1",
                            "execQty": "0.25",
                            "execPrice": "100.5",
                            "execFee": "0.01",
                            "feeCurrency": "USDT",
                            "execTime": "1700000000200",
                        }
                    ]
                },
            },
            ("POST", "/v5/position/trading-stop"): {
                "retCode": 0,
                "result": {},
            },
        }
    )
    adapter = BybitAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(long=True),
        clock=lambda: NOW,
    )
    order = SimpleNamespace(provider_order_id="ORD-1", client_order_id="e-1")

    fills = list(adapter.consume_fills(_intent(), order))
    protection = adapter.arm_protection(
        _intent(),
        order,
        filled_quantity=Decimal("0.25"),
    )

    assert len(fills) == 1
    assert fills[0].provider_fill_id == "F-1"
    assert fills[0].quantity == Decimal("0.25")
    assert fills[0].price == Decimal("100.5")
    assert fills[0].fee_amount == Decimal("0.01")
    assert fills[0].fee_currency == "USDT"
    assert protection.status == "ACTIVE"
    assert protection.provider_order_id == "bybit-position-stop:BTCUSDT"
    assert transport.calls[-1] == (
        "POST",
        "/v5/position/trading-stop",
        {
            "category": "linear",
            "symbol": "BTCUSDT",
            "stopLoss": "90",
            "tpslMode": "Full",
            "positionIdx": 0,
        },
    )


def test_provider_rejection_preserves_code_and_message():
    from app.execution.venues.bybit import BybitAdapter, BybitProviderError

    transport = RecordingTransport(
        {
            ("POST", "/v5/order/create"): {
                "retCode": 110007,
                "retMsg": "insufficient balance",
            }
        }
    )
    adapter = BybitAdapter(
        transport=transport,
        plan_resolver=lambda _: _plan(),
        clock=lambda: NOW,
    )

    with pytest.raises(BybitProviderError) as exc:
        adapter.submit(_intent(), client_order_id="e-1")

    assert exc.value.code == 110007
    assert "insufficient balance" in str(exc.value)