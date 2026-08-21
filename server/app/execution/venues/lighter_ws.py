"""Pure Lighter account WebSocket protocol normalization for SAI-071.

The module owns only subscription envelopes and decoding of provider account
order/trade messages into immutable facts.  It does not open a socket, create
an auth token, mutate execution state, submit transactions, arm protection or
reconcile ambiguous provider state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


class LighterWsProtocolError(ValueError):
    """Provider message does not satisfy the fail-closed account-stream contract."""


@dataclass(frozen=True, slots=True)
class LighterWsOrderEvent:
    event_key: str
    account_index: int
    market_index: int
    provider_order_id: str
    client_order_index: int
    status: str
    initial_quantity: Decimal
    remaining_quantity: Decimal
    filled_quantity: Decimal
    price: Decimal
    is_ask: bool
    reduce_only: bool
    event_at: datetime


@dataclass(frozen=True, slots=True)
class LighterWsFillEvent:
    event_key: str
    account_index: int
    market_index: int
    provider_trade_id: str
    client_order_index: int
    side: str
    quantity: Decimal
    price: Decimal
    is_maker: bool
    fee_raw: int | None
    event_at: datetime


@dataclass(frozen=True, slots=True)
class LighterWsBatch:
    orders: tuple[LighterWsOrderEvent, ...] = ()
    fills: tuple[LighterWsFillEvent, ...] = ()
    control: str | None = None


def _non_negative_int(field: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LighterWsProtocolError(f"{field} must be a non-negative integer")
    return value


def _account_index(value: Any) -> int:
    return _non_negative_int("account_index", value)


def _decimal(field: str, value: Any, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise LighterWsProtocolError(f"{field} must be a finite decimal")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise LighterWsProtocolError(f"{field} must be a finite decimal") from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        qualifier = "positive " if positive else "non-negative "
        raise LighterWsProtocolError(f"{field} must be a {qualifier}finite decimal")
    return parsed


def _bool(field: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise LighterWsProtocolError(f"{field} must be boolean")
    return value


def _string(field: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LighterWsProtocolError(f"{field} must be a non-empty string")
    return value.strip()


def _event_time(field: str, value: Any) -> datetime:
    millis = _non_negative_int(field, value)
    try:
        return datetime.fromtimestamp(millis / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise LighterWsProtocolError(f"{field} is outside timestamp range") from exc


def _stable_key(kind: str, payload: Mapping[str, Any]) -> str:
    def default(value: Any) -> str:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(type(value).__name__)

    rendered = json.dumps(
        {"kind": kind, **dict(payload)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=default,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _message(value: Mapping[str, Any] | str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise LighterWsProtocolError("message must be valid JSON") from exc
    elif isinstance(value, Mapping):
        decoded = dict(value)
    else:
        raise LighterWsProtocolError("message must be an object or JSON string")
    if not isinstance(decoded, dict):
        raise LighterWsProtocolError("message must decode to an object")
    return decoded


def _channel_account(channel: Any, *, prefix: str, expected: int) -> None:
    channel_value = _string("channel", channel)
    expected_channel = f"{prefix}:{expected}"
    if channel_value != expected_channel:
        raise LighterWsProtocolError(
            f"account channel mismatch: expected {expected_channel!r}, got {channel_value!r}"
        )


def build_lighter_account_subscriptions(
    *, account_index: int, auth_token: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the documented account order/trade subscription envelopes."""

    account = _account_index(account_index)
    auth = _string("auth_token", auth_token)
    return (
        {
            "type": "subscribe",
            "channel": f"account_all_orders/{account}",
            "auth": auth,
        },
        {
            "type": "subscribe",
            "channel": f"account_all_trades/{account}",
        },
    )


def _order_event(payload: Mapping[str, Any], *, account_index: int) -> LighterWsOrderEvent:
    owner = _non_negative_int("owner_account_index", payload.get("owner_account_index"))
    if owner != account_index:
        raise LighterWsProtocolError(
            f"account mismatch in order payload: expected {account_index}, got {owner}"
        )

    normalized = {
        "account_index": owner,
        "market_index": _non_negative_int("market_index", payload.get("market_index")),
        "provider_order_id": _string("order_id", payload.get("order_id")),
        "client_order_index": _non_negative_int(
            "client_order_index", payload.get("client_order_index")
        ),
        "status": _string("status", payload.get("status")),
        "initial_quantity": _decimal(
            "initial_base_amount", payload.get("initial_base_amount")
        ),
        "remaining_quantity": _decimal(
            "remaining_base_amount", payload.get("remaining_base_amount")
        ),
        "filled_quantity": _decimal(
            "filled_base_amount", payload.get("filled_base_amount")
        ),
        "price": _decimal("price", payload.get("price"), positive=True),
        "is_ask": _bool("is_ask", payload.get("is_ask")),
        "reduce_only": _bool("reduce_only", payload.get("reduce_only")),
        "event_at": _event_time("updated_at", payload.get("updated_at")),
    }
    event_key = _stable_key("ORDER", normalized)
    return LighterWsOrderEvent(event_key=event_key, **normalized)


def _optional_fee(field: str, payload: Mapping[str, Any]) -> int | None:
    value = payload.get(field)
    if value is None:
        return None
    return _non_negative_int(field, value)


def _fill_event(
    payload: Mapping[str, Any],
    *,
    account_index: int,
    side: str,
    client_order_index: int,
    is_maker: bool,
) -> LighterWsFillEvent:
    market_index = _non_negative_int("market_id", payload.get("market_id"))
    provider_trade_id = _string(
        "trade_id_str",
        payload.get("trade_id_str") or (
            str(payload["trade_id"]) if payload.get("trade_id") is not None else None
        ),
    )
    fee_field = "maker_fee" if is_maker else "taker_fee"
    normalized = {
        "account_index": account_index,
        "market_index": market_index,
        "provider_trade_id": provider_trade_id,
        "client_order_index": client_order_index,
        "side": side,
        "quantity": _decimal("size", payload.get("size"), positive=True),
        "price": _decimal("price", payload.get("price"), positive=True),
        "is_maker": is_maker,
        "fee_raw": _optional_fee(fee_field, payload),
        "event_at": _event_time("timestamp", payload.get("timestamp")),
    }
    event_key = _stable_key("FILL", normalized)
    return LighterWsFillEvent(event_key=event_key, **normalized)


def _flatten_grouped(
    field: str,
    value: Any,
) -> list[tuple[int | None, Mapping[str, Any]]]:
    if value is None:
        raise LighterWsProtocolError(f"{field} is required")
    if isinstance(value, list):
        result: list[tuple[int | None, Mapping[str, Any]]] = []
        for item in value:
            if not isinstance(item, Mapping):
                raise LighterWsProtocolError(f"{field} entries must be objects")
            result.append((None, item))
        return result
    if not isinstance(value, Mapping):
        raise LighterWsProtocolError(f"{field} must be a list or market map")

    result = []
    for market_key, entries in value.items():
        try:
            market_index = int(str(market_key))
        except (TypeError, ValueError) as exc:
            raise LighterWsProtocolError(f"{field} market key must be an integer") from exc
        if market_index < 0 or not isinstance(entries, list):
            raise LighterWsProtocolError(f"{field} market entries must be lists")
        for item in entries:
            if not isinstance(item, Mapping):
                raise LighterWsProtocolError(f"{field} entries must be objects")
            result.append((market_index, item))
    return result


def _decode_orders(message: Mapping[str, Any], *, account_index: int) -> LighterWsBatch:
    _channel_account(
        message.get("channel"), prefix="account_all_orders", expected=account_index
    )
    events: list[LighterWsOrderEvent] = []
    for grouped_market, payload in _flatten_grouped("orders", message.get("orders")):
        event = _order_event(payload, account_index=account_index)
        if grouped_market is not None and event.market_index != grouped_market:
            raise LighterWsProtocolError("market_index does not match orders market group")
        events.append(event)
    return LighterWsBatch(orders=tuple(events))


def _decode_trades(message: Mapping[str, Any], *, account_index: int) -> LighterWsBatch:
    _channel_account(
        message.get("channel"), prefix="account_all_trades", expected=account_index
    )
    fills: list[LighterWsFillEvent] = []
    for grouped_market, payload in _flatten_grouped("trades", message.get("trades")):
        market_id = _non_negative_int("market_id", payload.get("market_id"))
        if grouped_market is not None and market_id != grouped_market:
            raise LighterWsProtocolError("market_id does not match trades market group")

        ask_account = _non_negative_int("ask_account_id", payload.get("ask_account_id"))
        bid_account = _non_negative_int("bid_account_id", payload.get("bid_account_id"))
        maker_ask = _bool("is_maker_ask", payload.get("is_maker_ask"))
        matched = False

        if ask_account == account_index:
            matched = True
            fills.append(
                _fill_event(
                    payload,
                    account_index=account_index,
                    side="SELL",
                    client_order_index=_non_negative_int(
                        "ask_client_id", payload.get("ask_client_id")
                    ),
                    is_maker=maker_ask,
                )
            )
        if bid_account == account_index:
            matched = True
            fills.append(
                _fill_event(
                    payload,
                    account_index=account_index,
                    side="BUY",
                    client_order_index=_non_negative_int(
                        "bid_client_id", payload.get("bid_client_id")
                    ),
                    is_maker=not maker_ask,
                )
            )
        if not matched:
            raise LighterWsProtocolError(
                "account trade payload does not involve subscribed account"
            )

    return LighterWsBatch(fills=tuple(fills))


def decode_lighter_account_message(
    message: Mapping[str, Any] | str,
    *,
    account_index: int,
) -> LighterWsBatch:
    """Normalize one documented private-account WebSocket frame."""

    account = _account_index(account_index)
    decoded = _message(message)
    message_type = _string("type", decoded.get("type"))

    if message_type == "ping":
        return LighterWsBatch(control="PONG")
    if message_type in {"connected", "pong"}:
        return LighterWsBatch()
    if message_type in {
        "subscribed/account_all_orders",
        "update/account_all_orders",
    }:
        return _decode_orders(decoded, account_index=account)
    if message_type in {
        "subscribed/account_all_trades",
        "update/account_all_trades",
    }:
        return _decode_trades(decoded, account_index=account)
    raise LighterWsProtocolError(f"unsupported Lighter WebSocket message type: {message_type}")


__all__ = [
    "LighterWsBatch",
    "LighterWsFillEvent",
    "LighterWsOrderEvent",
    "LighterWsProtocolError",
    "build_lighter_account_subscriptions",
    "decode_lighter_account_message",
]
