"""Provider-confirmed T-Invest Sandbox LIMIT BUY -> SELL round trip.

This is deliberately not a generic order API. The server chooses from a small
compile-time allowlist, uses a dedicated sandbox account per scoped diagnostic
identity, submits one crossing LIMIT BUY and one crossing LIMIT SELL, and only
reports success after the provider confirms both fills and the position is flat.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.orm import Session

from .tinvest import TInvestProviderError, TInvestTransport
from .tinvest_transport import build_tinvest_sandbox_transport

_SMOKE_CANDIDATES = ("LQDT", "TBRU", "SBER")
_DIAGNOSTIC_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_ACCOUNT_PREFIX = "SignalAI roundtrip"
_PAY_IN_RUB = "100000"


@dataclass(frozen=True, slots=True)
class TInvestSandboxSmokeResult:
    round_trip_complete: bool
    symbol: str
    account_suffix: str
    buy_provider_order_id: str
    buy_execution_status: str
    buy_executed_lots: int
    sell_provider_order_id: str
    sell_execution_status: str
    sell_executed_lots: int
    position_flat: bool


def sandbox_smoke_request_id(diagnostic_key: str, *, leg: str = "buy") -> str:
    if not isinstance(diagnostic_key, str) or _DIAGNOSTIC_KEY_RE.fullmatch(diagnostic_key) is None:
        raise TInvestProviderError(
            code="INVALID_REQUEST",
            message="sandbox diagnostic key is invalid",
        )
    if leg not in {"buy", "sell"}:
        raise TInvestProviderError(
            code="INVALID_REQUEST",
            message="sandbox diagnostic leg is invalid",
        )
    return str(uuid5(NAMESPACE_URL, f"signalai:tinvest-sandbox-roundtrip:{diagnostic_key}:{leg}"))


def _account_name(diagnostic_key: str) -> str:
    suffix = hashlib.sha256(diagnostic_key.encode()).hexdigest()[:16]
    return f"{_ACCOUNT_PREFIX} {suffix}"


def _mapping(value: object, *, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TInvestProviderError(
            code="INVALID_RESPONSE",
            message=f"sandbox provider {context} response is invalid",
        )
    return value


def _non_negative_int(value: object, *, field: str) -> int:
    try:
        parsed = int(str(value or "0"))
    except (TypeError, ValueError) as exc:
        raise TInvestProviderError(
            code="INVALID_RESPONSE",
            message=f"sandbox provider {field} is invalid",
        ) from exc
    if parsed < 0:
        raise TInvestProviderError(
            code="INVALID_RESPONSE",
            message=f"sandbox provider {field} is invalid",
        )
    return parsed


def _open_account_id(transport: TInvestTransport, *, diagnostic_key: str) -> str:
    account_name = _account_name(diagnostic_key)
    response = _mapping(
        transport.call("SandboxService", "GetSandboxAccounts", {"status": "ACCOUNT_STATUS_OPEN"}),
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
            name = raw.get("name")
            if (
                isinstance(account_id, str)
                and account_id
                and not status.endswith("CLOSED")
                and name == account_name
            ):
                candidates.append(account_id)
    if candidates:
        return sorted(candidates)[0]

    opened = _mapping(
        transport.call("SandboxService", "OpenSandboxAccount", {"name": account_name}),
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
        uid = raw.get("uid")
        if (
            isinstance(ticker, str)
            and ticker.strip().upper() == symbol
            and isinstance(uid, str)
            and uid
        ):
            return uid
    return None


def _quotation(value: object, *, context: str) -> dict[str, object] | None:
    if not isinstance(value, list) or not value or not isinstance(value[0], Mapping):
        return None
    price = value[0].get("price")
    if not isinstance(price, Mapping):
        raise TInvestProviderError(
            code="INVALID_RESPONSE",
            message=f"sandbox provider {context} price is invalid",
        )
    return dict(price)


def _order_book_price(
    transport: TInvestTransport,
    *,
    instrument_uid: str,
    side: str,
) -> dict[str, object]:
    book = _mapping(
        transport.call(
            "MarketDataService",
            "GetOrderBook",
            {"instrumentId": instrument_uid, "depth": 1},
        ),
        context="order-book",
    )
    price = _quotation(book.get("asks" if side == "buy" else "bids"), context=side)
    if price is None:
        raise TInvestProviderError(
            code="MARKET_UNAVAILABLE",
            message=f"sandbox {side} order book side is empty",
        )
    return price


def _select_limit_tradeable_instrument(
    transport: TInvestTransport,
) -> tuple[str, str, dict[str, object]]:
    for symbol in _SMOKE_CANDIDATES:
        uid = _instrument_uid(transport, symbol=symbol)
        if uid is None:
            continue
        status = _mapping(
            transport.call(
                "MarketDataService",
                "GetTradingStatus",
                {"instrumentId": uid},
            ),
            context="trading-status",
        )
        if (
            status.get("apiTradeAvailableFlag") is True
            and status.get("limitOrderAvailableFlag") is True
        ):
            return symbol, uid, _order_book_price(
                transport,
                instrument_uid=uid,
                side="buy",
            )
    raise TInvestProviderError(
        code="MARKET_UNAVAILABLE",
        message="no sandbox round-trip candidate is available for API limit trading now",
    )


def _order_payload(
    *,
    account_id: str,
    instrument_uid: str,
    quantity: int,
    direction: str,
    request_id: str,
    price: dict[str, object],
) -> dict[str, object]:
    return {
        "accountId": account_id,
        "instrumentId": instrument_uid,
        "quantity": str(quantity),
        "direction": direction,
        "orderType": "ORDER_TYPE_LIMIT",
        "orderId": request_id,
        "priceType": "PRICE_TYPE_CURRENCY",
        "price": price,
        "timeInForce": "TIME_IN_FORCE_FILL_AND_KILL",
    }


def _reconcile_after_submit(
    transport: TInvestTransport,
    *,
    account_id: str,
    request_id: str,
    required_lots: int,
    attempts: int,
    sleeper: Callable[[float], None],
) -> Mapping[str, object] | None:
    """Require an independent state read; the write response is never proof."""

    last_state: Mapping[str, object] | None = None
    for attempt in range(attempts):
        if attempt:
            sleeper(0.5)
        state = _reconcile(transport, account_id=account_id, request_id=request_id)
        if state is None:
            continue
        last_state = state
        if _state_lots(state) >= required_lots:
            return state
        status = str(state.get("executionReportStatus") or "")
        if status.endswith("REJECTED") or status.endswith("CANCELLED"):
            return state
    return last_state


def _state_lots(state: Mapping[str, object] | None) -> int:
    if state is None:
        return 0
    return _non_negative_int(state.get("lotsExecuted"), field="lotsExecuted")


def _state_symbol(state: Mapping[str, object] | None, fallback: str = "UNKNOWN") -> str:
    if state is None:
        return fallback
    ticker = state.get("ticker")
    return ticker.strip().upper() if isinstance(ticker, str) and ticker.strip() else fallback


def _state_uid(state: Mapping[str, object]) -> str:
    uid = state.get("instrumentUid")
    if not isinstance(uid, str) or not uid:
        raise TInvestProviderError(
            code="INVALID_RESPONSE",
            message="sandbox provider filled order has no instrument uid",
        )
    return uid


def _position_is_flat(
    transport: TInvestTransport,
    *,
    account_id: str,
    instrument_uid: str,
) -> bool:
    positions = _mapping(
        transport.call(
            "SandboxService",
            "GetSandboxPositions",
            {"accountId": account_id},
        ),
        context="positions",
    )
    for collection in ("securities", "futures", "options"):
        raw_items = positions.get(collection)
        if raw_items is None:
            continue
        if not isinstance(raw_items, list):
            raise TInvestProviderError(
                code="INVALID_RESPONSE",
                message="sandbox provider positions response is invalid",
            )
        for raw in raw_items:
            if not isinstance(raw, Mapping) or raw.get("instrumentUid") != instrument_uid:
                continue
            balance = _non_negative_int(raw.get("balance"), field="position balance")
            blocked = _non_negative_int(raw.get("blocked"), field="position blocked")
            if balance != 0 or blocked != 0:
                return False
    return True


def _result(
    *,
    account_id: str,
    symbol: str,
    buy_state: Mapping[str, object] | None,
    sell_state: Mapping[str, object] | None,
    buy_request_id: str,
    sell_request_id: str,
    position_flat: bool,
) -> TInvestSandboxSmokeResult:
    buy_lots = _state_lots(buy_state)
    sell_lots = _state_lots(sell_state)
    return TInvestSandboxSmokeResult(
        round_trip_complete=(
            buy_lots > 0
            and sell_lots == buy_lots
            and position_flat
        ),
        symbol=_state_symbol(buy_state, symbol),
        account_suffix=account_id[-6:] if len(account_id) >= 6 else account_id,
        buy_provider_order_id=str((buy_state or {}).get("orderId") or buy_request_id),
        buy_execution_status=str(
            (buy_state or {}).get("executionReportStatus") or "NOT_CONFIRMED"
        ),
        buy_executed_lots=buy_lots,
        sell_provider_order_id=str((sell_state or {}).get("orderId") or sell_request_id),
        sell_execution_status=str(
            (sell_state or {}).get("executionReportStatus") or "NOT_CONFIRMED"
        ),
        sell_executed_lots=sell_lots,
        position_flat=position_flat,
    )


def run_tinvest_sandbox_smoke(
    session: Session,
    *,
    diagnostic_key: str,
    transport: TInvestTransport | None = None,
    reconciliation_attempts: int = 4,
    sleeper: Callable[[float], None] = time.sleep,
) -> TInvestSandboxSmokeResult:
    """Run or reconcile one idempotent provider-confirmed LIMIT round trip."""

    buy_id = sandbox_smoke_request_id(diagnostic_key, leg="buy")
    sell_id = sandbox_smoke_request_id(diagnostic_key, leg="sell")
    provider = transport or build_tinvest_sandbox_transport(session)
    account_id = _open_account_id(provider, diagnostic_key=diagnostic_key)
    attempts = max(1, min(int(reconciliation_attempts), 8))

    buy_state = _reconcile(provider, account_id=account_id, request_id=buy_id)
    symbol = _state_symbol(buy_state)
    if buy_state is None:
        symbol, instrument_uid, buy_price = _select_limit_tradeable_instrument(provider)
        provider.call(
            "SandboxService",
            "SandboxPayIn",
            {
                "accountId": account_id,
                "amount": {"currency": "rub", "units": _PAY_IN_RUB, "nano": 0},
            },
        )
        _mapping(
            provider.call(
                "SandboxService",
                "PostSandboxOrder",
                _order_payload(
                    account_id=account_id,
                    instrument_uid=instrument_uid,
                    quantity=1,
                    direction="ORDER_DIRECTION_BUY",
                    request_id=buy_id,
                    price=buy_price,
                ),
            ),
            context="buy-order",
        )
        buy_state = _reconcile_after_submit(
            provider,
            account_id=account_id,
            request_id=buy_id,
            required_lots=1,
            attempts=attempts,
            sleeper=sleeper,
        )

    buy_lots = _state_lots(buy_state)
    if buy_lots <= 0:
        return _result(
            account_id=account_id,
            symbol=symbol,
            buy_state=buy_state,
            sell_state=None,
            buy_request_id=buy_id,
            sell_request_id=sell_id,
            position_flat=False,
        )

    instrument_uid = _state_uid(buy_state)
    symbol = _state_symbol(buy_state, symbol)
    sell_state = _reconcile(provider, account_id=account_id, request_id=sell_id)
    if sell_state is None:
        sell_price = _order_book_price(
            provider,
            instrument_uid=instrument_uid,
            side="sell",
        )
        _mapping(
            provider.call(
                "SandboxService",
                "PostSandboxOrder",
                _order_payload(
                    account_id=account_id,
                    instrument_uid=instrument_uid,
                    quantity=buy_lots,
                    direction="ORDER_DIRECTION_SELL",
                    request_id=sell_id,
                    price=sell_price,
                ),
            ),
            context="sell-order",
        )
        sell_state = _reconcile_after_submit(
            provider,
            account_id=account_id,
            request_id=sell_id,
            required_lots=buy_lots,
            attempts=attempts,
            sleeper=sleeper,
        )

    sell_lots = _state_lots(sell_state)
    if sell_lots != buy_lots:
        return _result(
            account_id=account_id,
            symbol=symbol,
            buy_state=buy_state,
            sell_state=sell_state,
            buy_request_id=buy_id,
            sell_request_id=sell_id,
            position_flat=False,
        )

    position_flat = _position_is_flat(
        provider,
        account_id=account_id,
        instrument_uid=instrument_uid,
    )
    return _result(
        account_id=account_id,
        symbol=symbol,
        buy_state=buy_state,
        sell_state=sell_state,
        buy_request_id=buy_id,
        sell_request_id=sell_id,
        position_flat=position_flat,
    )


__all__ = [
    "TInvestSandboxSmokeResult",
    "run_tinvest_sandbox_smoke",
    "sandbox_smoke_request_id",
]
