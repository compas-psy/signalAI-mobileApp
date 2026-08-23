from __future__ import annotations

from collections import defaultdict

import pytest

from app.execution.venues.tinvest import TInvestProviderError
from app.execution.venues.tinvest_sandbox_smoke import (
    TInvestSandboxSmokeResult,
    run_tinvest_sandbox_smoke,
    sandbox_smoke_request_id,
)


class _ScriptedTransport:
    def __init__(self, script):
        self.script = {key: list(values) for key, values in script.items()}
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.counts = defaultdict(int)

    def call(self, service: str, method: str, body: dict[str, object]):
        key = (service, method)
        self.calls.append((service, method, body))
        self.counts[key] += 1
        if key not in self.script or not self.script[key]:
            raise AssertionError(f"unexpected provider call: {key}")
        value = self.script[key].pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _account():
    return {"accounts": [{"id": "sandbox-account-123456", "status": "ACCOUNT_STATUS_OPEN"}]}


def _filled_state(*, order_id: str = "exchange-order-1"):
    return {
        "orderId": order_id,
        "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
        "lotsExecuted": "1",
    }


def test_same_diagnostic_key_reconciles_existing_fill_without_duplicate_submit(session):
    key = "owner-smoke-2026-08-23-0001"
    request_id = sandbox_smoke_request_id(key)
    transport = _ScriptedTransport(
        {
            ("SandboxService", "GetSandboxAccounts"): [_account()],
            ("SandboxService", "GetSandboxOrderState"): [_filled_state()],
        }
    )

    result = run_tinvest_sandbox_smoke(session, diagnostic_key=key, transport=transport)

    assert isinstance(result, TInvestSandboxSmokeResult)
    assert result.filled is True
    assert result.executed_lots == 1
    assert result.symbol == "SBER"
    assert result.account_suffix == "123456"
    assert transport.counts[("SandboxService", "PostSandboxOrder")] == 0
    reconcile = next(
        body for service, method, body in transport.calls
        if (service, method) == ("SandboxService", "GetSandboxOrderState")
    )
    assert reconcile["orderId"] == request_id
    assert reconcile["orderIdType"] == "ORDER_ID_TYPE_REQUEST"


def test_new_smoke_submits_one_market_buy_then_confirms_provider_fill(session):
    key = "owner-smoke-2026-08-23-0002"
    not_found = TInvestProviderError.not_found()
    transport = _ScriptedTransport(
        {
            ("SandboxService", "GetSandboxAccounts"): [_account()],
            ("SandboxService", "GetSandboxOrderState"): [not_found, _filled_state()],
            ("SandboxService", "SandboxPayIn"): [{"balance": {"currency": "rub", "units": "100000", "nano": 0}}],
            ("InstrumentsService", "ShareBy"): [{"instrument": {"uid": "sber-uid", "ticker": "SBER"}}],
            ("MarketDataService", "GetTradingStatus"): [{
                "apiTradeAvailableFlag": True,
                "marketOrderAvailableFlag": True,
                "limitOrderAvailableFlag": True,
            }],
            ("SandboxService", "PostSandboxOrder"): [{
                "orderId": "exchange-order-1",
                "executionReportStatus": "EXECUTION_REPORT_STATUS_NEW",
                "lotsExecuted": "0",
            }],
        }
    )

    result = run_tinvest_sandbox_smoke(session, diagnostic_key=key, transport=transport)

    assert result.filled is True
    assert result.executed_lots == 1
    assert transport.counts[("SandboxService", "PostSandboxOrder")] == 1
    post = next(
        body for service, method, body in transport.calls
        if (service, method) == ("SandboxService", "PostSandboxOrder")
    )
    assert post == {
        "accountId": "sandbox-account-123456",
        "instrumentId": "sber-uid",
        "quantity": "1",
        "direction": "ORDER_DIRECTION_BUY",
        "orderType": "ORDER_TYPE_MARKET",
        "orderId": sandbox_smoke_request_id(key),
        "priceType": "PRICE_TYPE_CURRENCY",
    }
    assert all(service != "OrdersService" for service, _, _ in transport.calls)


def test_zero_executed_lots_is_not_reported_as_success(session):
    transport = _ScriptedTransport(
        {
            ("SandboxService", "GetSandboxAccounts"): [_account()],
            ("SandboxService", "GetSandboxOrderState"): [
                TInvestProviderError.not_found(),
                {
                    "orderId": "exchange-pending",
                    "executionReportStatus": "EXECUTION_REPORT_STATUS_NEW",
                    "lotsExecuted": "0",
                },
            ],
            ("SandboxService", "SandboxPayIn"): [{}],
            ("InstrumentsService", "ShareBy"): [{"instrument": {"uid": "sber-uid"}}],
            ("MarketDataService", "GetTradingStatus"): [{
                "apiTradeAvailableFlag": True,
                "marketOrderAvailableFlag": True,
                "limitOrderAvailableFlag": True,
            }],
            ("SandboxService", "PostSandboxOrder"): [{"orderId": "exchange-pending"}],
        }
    )

    result = run_tinvest_sandbox_smoke(
        session,
        diagnostic_key="owner-smoke-2026-08-23-0003",
        transport=transport,
        reconciliation_attempts=1,
        sleeper=lambda _: None,
    )
    assert result.filled is False
    assert result.executed_lots == 0


def test_missing_server_credential_fails_closed_before_provider_io(session):
    with pytest.raises(TInvestProviderError) as captured:
        run_tinvest_sandbox_smoke(
            session,
            diagnostic_key="owner-smoke-2026-08-23-0004",
        )
    assert captured.value.code == "CREDENTIAL_MISSING"


def test_invalid_diagnostic_key_fails_before_provider_call(session):
    transport = _ScriptedTransport({})
    with pytest.raises(TInvestProviderError) as captured:
        run_tinvest_sandbox_smoke(session, diagnostic_key="short", transport=transport)
    assert captured.value.code == "INVALID_REQUEST"
    assert transport.calls == []
