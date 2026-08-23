"""Narrow provider-confirmed T-Invest Sandbox smoke transaction.

This module is intentionally not a generic order API. It buys exactly one lot
from a small compile-time allowlist of diagnostic instruments, choosing the
first one that T-Invest currently reports as API-tradeable. It uses a stable
provider request id for replay safety and reconciles before every possible
submit. It has no live host, account, credential slot or execution-mode side
effect.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.orm import Session

from .tinvest import TInvestProviderError, TInvestTransport
from .tinvest_transport import build_tinvest_sandbox_transport

# Prefer instruments that can be available in extended/weekend sessions, while
# retaining SBER as a conventional weekday fallback. This is intentionally a
# fixed server-side allowlist rather than a user-supplied ticker/order surface.
_SMOKE_CANDIDATES = ("LQDT", "TBRU", "SBER")
_DIAGNOSTIC_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_ACCOUNT_NAME = "SignalAI thin-client smoke"
_PAY_IN_RUB = "100000"


@dataclass(frozen=True, slots=True)
class TInvestSandboxSmokeResult:
    filled: bool
    symbol: str
    account_suffix: str
    provider_order_id: str
    execution_status: str
    executed_lots: int


def sandbox_smoke_request_id(diagnostic_key: str) -> str:
    if not isinstance(diagnostic_key, str) or _DIAGNOSTIC_KEY_RE.fullmatch(diagnostic_key) is None:
        raise TInvestProviderError(
            code="INVALID_REQUEST",
            message="sandbox diagnostic key is invalid",
        )
    return str(uuid5(NAMESPACE_URL, f"signalai:tinvest-sandbox-smoke:{diagnostic_key}"))


def _mapping(value: object, *, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TInvestProviderError(
            code="INVALID_RESPONSE",
            message=f"sandbox provider {context} response is invalid",
        )
    return value


def _positive_lots(value: object) -> int:
    try:
        parsed = int(str(value or "0"))
    except (TypeError, ValueError) as exc:
        raise TInvestProviderError(
            code="INVALID_RESPONSE",
            message="sandbox provider lotsExecuted is invalid",
        ) from exc
    if parsed < 0:
        raise TInvestProviderError(
            code="INVALID_RESPONSE",
            message="sandbox provider lotsExecuted is invalid",
        )
    return parsed


def _result(
    account_id: str,
    state: Mapping[str, object],
    request_id: str,
    *,
    symbol: str | None = None,
) -> TInvestSandboxSmokeResult:
    executed = _positive_lots(state.get("lotsExecuted"))
    provider_symbol = state.get("ticker")
    resolved_symbol = (
        provider_symbol.strip().upper()
        if isinstance(provider_symbol, str) and provider_symbol.strip()
        else symbol or "UNKNOWN"
    )
    return TInvestSandboxSmokeResult(
        filled=executed > 0,
        symbol=resolved_symbol,
        account_suffix=account_id[-6:] if len(account_id) >= 6 else account_id,
        provider_order_id=str(state.get("orderId") or request_id),
        execution_status=str(state.get("executionReportStatus") or "UNKNOWN"),
        executed_lots=executed,
    )


def _open_account_id(transport: TInvestTransport) -> str:
    response = _mapping(
        transport.call("SandboxService", "GetSandboxAccounts", {}),
        context="accounts",
    )
    raw_accounts = response.get("accounts")
    candidates: list[str] = []
    if isinstance(raw_accounts, list):
        for raw in raw_accounts:
            if not isinstance(raw, Mapping):
                continue
            account_id = raw.get("id")
            status = str(raw.get("status") or "")
            if isinstance(account_id, str) and account_id and not status.endswith("CLOSED"):
                candidates.append(account_id)
    if candidates:
        return sorted(candidates)[0]

    opened = _mapping(
        transport.call(
            "SandboxService",
            "OpenSandboxAccount",
            {"name": _ACCOUNT_NAME},
        ),
        context="open-account",
    )
    account_id = opened.get("accountId")
    if not isinstance(account_id, str) or not account_id:
        raise TInvestProviderError(
            code="INVALID_RESPONSE",
            message="sandbox provider did not return account id",
        )
    return account_id


def _reconcile(
    transport: TInvestTransport,
    *,
    account_id: str,
    request_id: str,
) -> Mapping[str, object] | None:
    try:
        return _mapping(
            transport.call(
                "SandboxService",
                "GetSandboxOrderState",
                {
                    "accountId": account_id,
                    "orderId": request_id,
                    "orderIdType": "ORDER_ID_TYPE_REQUEST",
                    "priceType": "PRICE_TYPE_CURRENCY",
                },
            ),
            context="order-state",
        )
    except TInvestProviderError as exc:
        if exc.is_not_found:
            return None
        raise


def _instrument_uid(transport: TInvestTransport, *, symbol: str) -> str | None:
    response = _mapping(
        transport.call(
            "InstrumentsService",
            "FindInstrument",
            {"query": symbol, "apiTradeAvailableFlag": True},
        ),
        context="instrument-search",
    )
    instruments = response.get("instruments")
    if not isinstance(instruments, list):
        raise TInvestProviderError(
            code="INVALID_RESPONSE",
            message="sandbox provider instrument search response is invalid",
        )

    for raw in instruments:
        if not isinstance(raw, Mapping):
            continue
        ticker = raw.get("ticker")
        if not isinstance(ticker, str) or ticker.strip().upper() != symbol:
            continue
        uid = raw.get("uid")
        if isinstance(uid, str) and uid:
            return uid
    return None


def _base_order_payload(
    *,
    account_id: str,
    instrument_uid: str,
    request_id: str,
) -> dict[str, object]:
    return {
        "accountId": account_id,
        "instrumentId": instrument_uid,
        "quantity": "1",
        "direction": "ORDER_DIRECTION_BUY",
        "orderId": request_id,
        "priceType": "PRICE_TYPE_CURRENCY",
    }


def _tradeable_order_payload(
    transport: TInvestTransport,
    *,
    account_id: str,
    request_id: str,
) -> tuple[str, dict[str, object]]:
    """Choose the first allowlisted instrument that is actually tradeable now."""

    for symbol in _SMOKE_CANDIDATES:
        instrument_uid = _instrument_uid(transport, symbol=symbol)
        if instrument_uid is None:
            continue

        status = _mapping(
            transport.call(
                "MarketDataService",
                "GetTradingStatus",
                {"instrumentId": instrument_uid},
            ),
            context="trading-status",
        )
        if status.get("apiTradeAvailableFlag") is not True:
            continue

        base = _base_order_payload(
            account_id=account_id,
            instrument_uid=instrument_uid,
            request_id=request_id,
        )
        if status.get("marketOrderAvailableFlag") is True:
            return symbol, {**base, "orderType": "ORDER_TYPE_MARKET"}

        if status.get("limitOrderAvailableFlag") is not True:
            continue
        book = _mapping(
            transport.call(
                "MarketDataService",
                "GetOrderBook",
                {"instrumentId": instrument_uid, "depth": 1},
            ),
            context="order-book",
        )
        asks = book.get("asks")
        if not isinstance(asks, list) or not asks or not isinstance(asks[0], Mapping):
            continue
        price = asks[0].get("price")
        if not isinstance(price, Mapping):
            raise TInvestProviderError(
                code="INVALID_RESPONSE",
                message="sandbox provider ask price is invalid",
            )
        return symbol, {
            **base,
            "orderType": "ORDER_TYPE_LIMIT",
            "price": dict(price),
            "timeInForce": "TIME_IN_FORCE_FILL_AND_KILL",
        }

    raise TInvestProviderError(
        code="MARKET_UNAVAILABLE",
        message="no sandbox smoke candidate is available for API trading now",
    )


def run_tinvest_sandbox_smoke(
    session: Session,
    *,
    diagnostic_key: str,
    transport: TInvestTransport | None = None,
    reconciliation_attempts: int = 4,
    sleeper: Callable[[float], None] = time.sleep,
) -> TInvestSandboxSmokeResult:
    """Submit at most one sandbox BUY and return provider-reconciled evidence."""

    request_id = sandbox_smoke_request_id(diagnostic_key)
    provider = transport or build_tinvest_sandbox_transport(session)
    account_id = _open_account_id(provider)

    existing = _reconcile(provider, account_id=account_id, request_id=request_id)
    if existing is not None:
        return _result(account_id, existing, request_id)

    symbol, payload = _tradeable_order_payload(
        provider,
        account_id=account_id,
        request_id=request_id,
    )

    # Virtual funds only. This call exists solely in SandboxService and is
    # deliberately made only after proving no order already exists and after a
    # currently tradeable allowlisted instrument has been selected.
    provider.call(
        "SandboxService",
        "SandboxPayIn",
        {
            "accountId": account_id,
            "amount": {"currency": "rub", "units": _PAY_IN_RUB, "nano": 0},
        },
    )
    submitted = _mapping(
        provider.call("SandboxService", "PostSandboxOrder", payload),
        context="post-order",
    )

    attempts = max(1, min(int(reconciliation_attempts), 8))
    for attempt in range(attempts):
        if attempt:
            sleeper(0.5)
        state = _reconcile(provider, account_id=account_id, request_id=request_id)
        if state is not None:
            return _result(account_id, state, request_id, symbol=symbol)

    # Provider accepted the submit but the reconciliation read has not become
    # visible yet. Never re-submit here. The same diagnostic key can be retried
    # later and will reconcile before any new submit is possible.
    return _result(account_id, submitted, request_id, symbol=symbol)


__all__ = [
    "TInvestSandboxSmokeResult",
    "run_tinvest_sandbox_smoke",
    "sandbox_smoke_request_id",
]
