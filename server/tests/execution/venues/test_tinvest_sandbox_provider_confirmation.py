from __future__ import annotations

from collections import defaultdict

from app.execution.venues.tinvest import TInvestProviderError
from app.execution.venues.tinvest_sandbox_smoke import (
    _account_name,
    run_tinvest_sandbox_smoke,
    sandbox_smoke_request_id,
)


class _Transport:
    def __init__(self, key: str):
        self.key = key
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.counts = defaultdict(int)
        self.order_state_reads = 0

    def call(self, service: str, method: str, body: dict[str, object]):
        self.calls.append((service, method, body))
        self.counts[(service, method)] += 1
        if (service, method) == ("SandboxService", "GetSandboxAccounts"):
            return {
                "accounts": [
                    {
                        "id": "sandbox-account-123456",
                        "status": "ACCOUNT_STATUS_OPEN",
                        "name": _account_name(self.key),
                    }
                ]
            }
        if (service, method) == ("SandboxService", "GetSandboxOrderState"):
            self.order_state_reads += 1
            raise TInvestProviderError.not_found()
        if (service, method) == ("InstrumentsService", "FindInstrument"):
            return {
                "instruments": [
                    {
                        "ticker": "LQDT",
                        "uid": "lqdt-uid",
                        "apiTradeAvailableFlag": True,
                    }
                ]
            }
        if (service, method) == ("MarketDataService", "GetTradingStatus"):
            return {
                "apiTradeAvailableFlag": True,
                "limitOrderAvailableFlag": True,
                "marketOrderAvailableFlag": True,
            }
        if (service, method) == ("MarketDataService", "GetOrderBook"):
            return {
                "bids": [{"price": {"units": "99", "nano": 0}}],
                "asks": [{"price": {"units": "101", "nano": 0}}],
            }
        if (service, method) == ("SandboxService", "SandboxPayIn"):
            return {}
        if (service, method) == ("SandboxService", "PostSandboxOrder"):
            # The write response claims a fill, but the independent state read
            # is deliberately still NOT_FOUND. This must never become readiness.
            return {
                "orderId": sandbox_smoke_request_id(self.key, leg="buy"),
                "ticker": "LQDT",
                "instrumentUid": "lqdt-uid",
                "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
                "lotsExecuted": "1",
            }
        raise AssertionError(f"unexpected provider call: {(service, method)}")


def test_post_order_fill_without_get_order_state_confirmation_is_not_accepted(session):
    key = "provider-confirmation-2026-08-23"
    transport = _Transport(key)

    result = run_tinvest_sandbox_smoke(
        session,
        diagnostic_key=key,
        transport=transport,
        reconciliation_attempts=2,
        sleeper=lambda _: None,
    )

    assert result.round_trip_complete is False
    assert result.buy_executed_lots == 0
    assert result.sell_executed_lots == 0
    assert transport.counts[("SandboxService", "PostSandboxOrder")] == 1
    assert transport.counts[("SandboxService", "GetSandboxPositions")] == 0
