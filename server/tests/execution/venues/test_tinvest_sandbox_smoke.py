from __future__ import annotations

from collections import defaultdict

import pytest

from app.execution.venues.tinvest import TInvestProviderError
from app.execution.venues.tinvest_sandbox_smoke import (
    TInvestSandboxSmokeResult,
    _account_name,
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


def _account(key: str):
    return {
        "accounts": [
            {
                "id": "sandbox-account-123456",
                "status": "ACCOUNT_STATUS_OPEN",
                "name": _account_name(key),
            }
        ]
    }


def _filled_state(
    *,
    order_id: str,
    ticker: str = "LQDT",
    uid: str = "lqdt-uid",
    lots: int = 1,
):
    return {
        "orderId": order_id,
        "ticker": ticker,
        "instrumentUid": uid,
        "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
        "lotsExecuted": str(lots),
    }


def _pending_state(*, order_id: str, ticker: str = "LQDT", uid: str = "lqdt-uid"):
    return {
        "orderId": order_id,
        "ticker": ticker,
        "instrumentUid": uid,
        "executionReportStatus": "EXECUTION_REPORT_STATUS_NEW",
        "lotsExecuted": "0",
    }


def _found(ticker: str, uid: str):
    return {
        "instruments": [
            {
                "ticker": ticker,
                "uid": uid,
                "apiTradeAvailableFlag": True,
            }
        ]
    }


def _status(*, api: bool, limit: bool, market: bool = True):
    return {
        "apiTradeAvailableFlag": api,
        "marketOrderAvailableFlag": market,
        "limitOrderAvailableFlag": limit,
    }


def _book(*, bid_units: str = "99", ask_units: str = "101"):
    return {
        "bids": [{"price": {"units": bid_units, "nano": 0}, "quantity": "10"}],
        "asks": [{"price": {"units": ask_units, "nano": 0}, "quantity": "10"}],
    }


def _flat_positions():
    return {"securities": []}


def test_round_trip_uses_crossing_limit_buy_then_sell_and_confirms_flat(session):
    key = "owner-roundtrip-2026-08-23-0001"
    buy_id = sandbox_smoke_request_id(key, leg="buy")
    sell_id = sandbox_smoke_request_id(key, leg="sell")
    transport = _ScriptedTransport(
        {
            ("SandboxService", "GetSandboxAccounts"): [_account(key)],
            ("SandboxService", "GetSandboxOrderState"): [
                TInvestProviderError.not_found(),
                _filled_state(order_id=buy_id),
                TInvestProviderError.not_found(),
                _filled_state(order_id=sell_id),
            ],
            ("InstrumentsService", "FindInstrument"): [_found("LQDT", "lqdt-uid")],
            ("MarketDataService", "GetTradingStatus"): [_status(api=True, limit=True)],
            ("MarketDataService", "GetOrderBook"): [_book(), _book()],
            ("SandboxService", "SandboxPayIn"): [{}],
            ("SandboxService", "PostSandboxOrder"): [
                _pending_state(order_id=buy_id),
                _pending_state(order_id=sell_id),
            ],
            ("SandboxService", "GetSandboxPositions"): [_flat_positions()],
        }
    )

    result = run_tinvest_sandbox_smoke(session, diagnostic_key=key, transport=transport)

    assert isinstance(result, TInvestSandboxSmokeResult)
    assert result.round_trip_complete is True
    assert result.position_flat is True
    assert result.symbol == "LQDT"
    assert result.buy_executed_lots == 1
    assert result.sell_executed_lots == 1
    assert result.buy_provider_order_id == buy_id
    assert result.sell_provider_order_id == sell_id
    assert result.account_suffix == "123456"

    posts = [
        body for service, method, body in transport.calls
        if (service, method) == ("SandboxService", "PostSandboxOrder")
    ]
    assert posts == [
        {
            "accountId": "sandbox-account-123456",
            "instrumentId": "lqdt-uid",
            "quantity": "1",
            "direction": "ORDER_DIRECTION_BUY",
            "orderType": "ORDER_TYPE_LIMIT",
            "orderId": buy_id,
            "priceType": "PRICE_TYPE_CURRENCY",
            "price": {"units": "101", "nano": 0},
            "timeInForce": "TIME_IN_FORCE_FILL_AND_KILL",
        },
        {
            "accountId": "sandbox-account-123456",
            "instrumentId": "lqdt-uid",
            "quantity": "1",
            "direction": "ORDER_DIRECTION_SELL",
            "orderType": "ORDER_TYPE_LIMIT",
            "orderId": sell_id,
            "priceType": "PRICE_TYPE_CURRENCY",
            "price": {"units": "99", "nano": 0},
            "timeInForce": "TIME_IN_FORCE_FILL_AND_KILL",
        },
    ]


def test_replay_of_completed_round_trip_reconciles_both_legs_without_duplicate_submit(session):
    key = "owner-roundtrip-2026-08-23-0002"
    buy_id = sandbox_smoke_request_id(key, leg="buy")
    sell_id = sandbox_smoke_request_id(key, leg="sell")
    transport = _ScriptedTransport(
        {
            ("SandboxService", "GetSandboxAccounts"): [_account(key)],
            ("SandboxService", "GetSandboxOrderState"): [
                _filled_state(order_id=buy_id),
                _filled_state(order_id=sell_id),
            ],
            ("SandboxService", "GetSandboxPositions"): [_flat_positions()],
        }
    )

    result = run_tinvest_sandbox_smoke(session, diagnostic_key=key, transport=transport)

    assert result.round_trip_complete is True
    assert transport.counts[("SandboxService", "PostSandboxOrder")] == 0
    assert transport.counts[("SandboxService", "SandboxPayIn")] == 0


def test_unavailable_first_candidate_falls_through_to_next_limit_tradeable_candidate(session):
    key = "owner-roundtrip-2026-08-23-0003"
    buy_id = sandbox_smoke_request_id(key, leg="buy")
    sell_id = sandbox_smoke_request_id(key, leg="sell")
    transport = _ScriptedTransport(
        {
            ("SandboxService", "GetSandboxAccounts"): [_account(key)],
            ("SandboxService", "GetSandboxOrderState"): [
                TInvestProviderError.not_found(),
                _filled_state(order_id=buy_id, ticker="TBRU", uid="tbru-uid"),
                TInvestProviderError.not_found(),
                _filled_state(order_id=sell_id, ticker="TBRU", uid="tbru-uid"),
            ],
            ("InstrumentsService", "FindInstrument"): [
                _found("LQDT", "lqdt-uid"),
                _found("TBRU", "tbru-uid"),
            ],
            ("MarketDataService", "GetTradingStatus"): [
                _status(api=False, limit=False),
                _status(api=True, limit=True),
            ],
            ("MarketDataService", "GetOrderBook"): [_book(), _book()],
            ("SandboxService", "SandboxPayIn"): [{}],
            ("SandboxService", "PostSandboxOrder"): [
                _pending_state(order_id=buy_id, ticker="TBRU", uid="tbru-uid"),
                _pending_state(order_id=sell_id, ticker="TBRU", uid="tbru-uid"),
            ],
            ("SandboxService", "GetSandboxPositions"): [_flat_positions()],
        }
    )

    result = run_tinvest_sandbox_smoke(session, diagnostic_key=key, transport=transport)

    assert result.round_trip_complete is True
    assert result.symbol == "TBRU"
    queries = [
        body["query"] for service, method, body in transport.calls
        if (service, method) == ("InstrumentsService", "FindInstrument")
    ]
    assert queries == ["LQDT", "TBRU"]


def test_unfilled_buy_never_submits_sell_and_is_not_round_trip_success(session):
    key = "owner-roundtrip-2026-08-23-0004"
    buy_id = sandbox_smoke_request_id(key, leg="buy")
    transport = _ScriptedTransport(
        {
            ("SandboxService", "GetSandboxAccounts"): [_account(key)],
            ("SandboxService", "GetSandboxOrderState"): [
                TInvestProviderError.not_found(),
                _pending_state(order_id=buy_id),
            ],
            ("InstrumentsService", "FindInstrument"): [_found("LQDT", "lqdt-uid")],
            ("MarketDataService", "GetTradingStatus"): [_status(api=True, limit=True)],
            ("MarketDataService", "GetOrderBook"): [_book()],
            ("SandboxService", "SandboxPayIn"): [{}],
            ("SandboxService", "PostSandboxOrder"): [_pending_state(order_id=buy_id)],
        }
    )

    result = run_tinvest_sandbox_smoke(
        session,
        diagnostic_key=key,
        transport=transport,
        reconciliation_attempts=1,
        sleeper=lambda _: None,
    )

    assert result.round_trip_complete is False
    assert result.buy_executed_lots == 0
    assert result.sell_executed_lots == 0
    assert transport.counts[("SandboxService", "PostSandboxOrder")] == 1
    assert transport.counts[("SandboxService", "GetSandboxPositions")] == 0


def test_filled_buy_but_unfilled_sell_is_not_round_trip_success(session):
    key = "owner-roundtrip-2026-08-23-0005"
    buy_id = sandbox_smoke_request_id(key, leg="buy")
    sell_id = sandbox_smoke_request_id(key, leg="sell")
    transport = _ScriptedTransport(
        {
            ("SandboxService", "GetSandboxAccounts"): [_account(key)],
            ("SandboxService", "GetSandboxOrderState"): [
                _filled_state(order_id=buy_id),
                TInvestProviderError.not_found(),
                _pending_state(order_id=sell_id),
            ],
            ("MarketDataService", "GetOrderBook"): [_book()],
            ("SandboxService", "PostSandboxOrder"): [_pending_state(order_id=sell_id)],
        }
    )

    result = run_tinvest_sandbox_smoke(
        session,
        diagnostic_key=key,
        transport=transport,
        reconciliation_attempts=1,
        sleeper=lambda _: None,
    )

    assert result.round_trip_complete is False
    assert result.buy_executed_lots == 1
    assert result.sell_executed_lots == 0
    assert transport.counts[("SandboxService", "PostSandboxOrder")] == 1
    assert transport.counts[("SandboxService", "GetSandboxPositions")] == 0


def test_non_flat_position_fails_round_trip_even_after_both_fills(session):
    key = "owner-roundtrip-2026-08-23-0006"
    buy_id = sandbox_smoke_request_id(key, leg="buy")
    sell_id = sandbox_smoke_request_id(key, leg="sell")
    transport = _ScriptedTransport(
        {
            ("SandboxService", "GetSandboxAccounts"): [_account(key)],
            ("SandboxService", "GetSandboxOrderState"): [
                _filled_state(order_id=buy_id),
                _filled_state(order_id=sell_id),
            ],
            ("SandboxService", "GetSandboxPositions"): [
                {
                    "securities": [
                        {
                            "instrumentUid": "lqdt-uid",
                            "ticker": "LQDT",
                            "balance": "1",
                            "blocked": "0",
                        }
                    ]
                }
            ],
        }
    )

    result = run_tinvest_sandbox_smoke(session, diagnostic_key=key, transport=transport)

    assert result.position_flat is False
    assert result.round_trip_complete is False


def test_missing_server_credential_fails_closed_before_provider_io(session):
    with pytest.raises(TInvestProviderError) as captured:
        run_tinvest_sandbox_smoke(
            session,
            diagnostic_key="owner-roundtrip-2026-08-23-0007",
        )
    assert captured.value.code == "CREDENTIAL_MISSING"


def test_invalid_diagnostic_key_fails_before_provider_call(session):
    transport = _ScriptedTransport({})
    with pytest.raises(TInvestProviderError) as captured:
        run_tinvest_sandbox_smoke(session, diagnostic_key="short", transport=transport)
    assert captured.value.code == "INVALID_REQUEST"
    assert transport.calls == []
