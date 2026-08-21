"""Replay-safe Lighter order actions and position protection for SAI-070/072.

This is deliberately a narrow provider-action boundary.  It converts validated
Decimal order facts to Lighter's integer wire representation and coordinates
SAI-069's durable order identity/nonce state around an injected transport.
Private streams are normalized separately; reconciliation and execution-mode
activation are owned by later slices.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Callable, Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ...models.lighter_execution import (
    LighterNonceReservation as NonceReservation,
    LighterOrderActionBinding as OrderActionBinding,
    LighterOrderIdentity as OrderIdentity,
)
from .lighter_facts import LighterMarketFact
from .lighter_replay import (
    LighterReplayError,
    mark_lighter_nonce_consumed,
    reserve_lighter_nonce,
    resolve_lighter_order_identity,
)

# Verified against Lighter SignerClient constants. Keeping them local avoids a
# runtime SDK dependency while preserving the provider wire contract.
_ORDER_TYPE_LIMIT = 0
_ORDER_TYPE_MARKET = 1
_ORDER_TYPE_STOP_LOSS_LIMIT = 3
_TIF_IOC = 0
_TIF_GTT = 1
_TIF_POST_ONLY = 2
_NIL_TRIGGER_PRICE = 0
_DEFAULT_LIMIT_EXPIRY = -1
_DEFAULT_IOC_EXPIRY = 0
_SKIP_NONCE_ON = 1
_INT64_MAX = (1 << 63) - 1
_UINT32_MAX = (1 << 32) - 1


class LighterOrderActionError(RuntimeError):
    """Base fail-closed error for the Lighter order-action boundary."""


class LighterActionReplayMismatch(LighterOrderActionError):
    """The same durable action identity was reused with a changed request."""


class LighterActionAlreadyConsumed(LighterOrderActionError):
    """A provider action already has explicit consumed nonce evidence."""


class LighterActionRejected(LighterOrderActionError):
    """The provider returned a non-success acknowledgement."""


@dataclass(frozen=True, slots=True)
class LighterActionAck:
    code: int
    tx_hash: str
    message: str | None = None


class LighterActionTransport(Protocol):
    account_index: int
    api_key_index: int

    def next_nonce(self) -> int: ...

    def create_order(self, **kwargs: Any) -> LighterActionAck: ...

    def cancel_order(self, **kwargs: Any) -> LighterActionAck: ...


SessionFactory = Callable[[], Session]


def _non_negative_int(field: str, value: Any, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LighterOrderActionError(f"{field} must be an integer")
    if value < 0 or value > maximum:
        raise LighterOrderActionError(f"{field} is outside provider range")
    return value


def _transport_scope(transport: LighterActionTransport) -> tuple[int, int]:
    account_index = _non_negative_int(
        "account_index", getattr(transport, "account_index", None), maximum=_INT64_MAX
    )
    api_key_index = _non_negative_int(
        "api_key_index", getattr(transport, "api_key_index", None), maximum=253
    )
    return account_index, api_key_index


def _validate_market(market: LighterMarketFact) -> None:
    if not isinstance(market, LighterMarketFact):
        raise LighterOrderActionError("market must be a normalized LighterMarketFact")
    if market.status != "active":
        raise LighterOrderActionError("Lighter market is not active")
    _non_negative_int("market_id", market.market_id, maximum=(1 << 31) - 1)
    for name, precision in (
        ("size_decimals", market.size_decimals),
        ("price_decimals", market.price_decimals),
    ):
        _non_negative_int(name, precision, maximum=18)
    if market.min_base_amount < 0 or market.min_quote_amount < 0:
        raise LighterOrderActionError("market minimums cannot be negative")
    if market.order_quote_limit <= 0:
        raise LighterOrderActionError("market order_quote_limit must be positive")


def _positive_decimal(field: str, value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise LighterOrderActionError(f"{field} must be a positive finite Decimal")
    return value


def _scale_exact(
    field: str,
    value: Decimal,
    *,
    decimals: int,
    maximum: int,
) -> int:
    parsed = _positive_decimal(field, value)
    scaled = parsed * (Decimal(10) ** decimals)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise LighterOrderActionError(
            f"{field} cannot be represented exactly at provider precision"
        )
    result = int(integral)
    if result <= 0 or result > maximum:
        raise LighterOrderActionError(f"{field} is outside provider integer range")
    return result


def _validate_order_values(
    market: LighterMarketFact,
    *,
    quantity: Decimal,
    price: Decimal,
) -> tuple[int, int]:
    _validate_market(market)
    parsed_quantity = _positive_decimal("quantity", quantity)
    parsed_price = _positive_decimal("price", price)
    if parsed_quantity < market.min_base_amount:
        raise LighterOrderActionError("quantity is below Lighter minimum base amount")
    quote_amount = parsed_quantity * parsed_price
    if quote_amount < market.min_quote_amount:
        raise LighterOrderActionError("order value is below Lighter minimum quote amount")
    if quote_amount > market.order_quote_limit:
        raise LighterOrderActionError("order value exceeds Lighter quote limit")
    base_amount = _scale_exact(
        "quantity",
        parsed_quantity,
        decimals=market.size_decimals,
        maximum=_INT64_MAX,
    )
    wire_price = _scale_exact(
        "price",
        parsed_price,
        decimals=market.price_decimals,
        maximum=_UINT32_MAX,
    )
    return base_amount, wire_price


def _canonical_hash(payload: dict[str, Any]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _advisory_key(namespace: str, identity: str) -> int:
    digest = hashlib.sha256(f"{namespace}:{identity}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], byteorder="big", signed=False) & _INT64_MAX
    return value or 1


def _lock(db: Session, namespace: str, identity: str) -> None:
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _advisory_key(namespace, identity)},
    )


def _existing_identity(
    db: Session,
    *,
    account_index: int,
    client_order_id: str,
) -> OrderIdentity:
    if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 96:
        raise LighterOrderActionError("client_order_id must be a non-empty string up to 96 chars")
    identity = db.scalar(
        select(OrderIdentity).where(OrderIdentity.client_order_id == client_order_id)
    )
    if identity is None:
        raise LighterOrderActionError("cancel requires an existing Lighter order identity")
    if identity.account_index != account_index:
        raise LighterOrderActionError(
            "client_order_id is bound to a different Lighter account"
        )
    return identity


def _bind_request(
    db: Session,
    *,
    action_key: str,
    action_type: str,
    account_index: int,
    api_key_index: int,
    identity: OrderIdentity,
    market_index: int,
    request_hash: str,
) -> OrderActionBinding:
    existing = db.scalar(
        select(OrderActionBinding).where(OrderActionBinding.action_key == action_key)
    )
    if existing is not None:
        unchanged = (
            existing.action_type == action_type
            and existing.account_index == account_index
            and existing.api_key_index == api_key_index
            and existing.client_order_id == identity.client_order_id
            and existing.client_order_index == identity.client_order_index
            and existing.market_index == market_index
            and existing.request_hash == request_hash
        )
        if not unchanged:
            raise LighterActionReplayMismatch(
                f"{action_key} is already bound to a different request"
            )
        return existing

    binding = OrderActionBinding(
        action_key=action_key,
        action_type=action_type,
        account_index=account_index,
        api_key_index=api_key_index,
        client_order_id=identity.client_order_id,
        client_order_index=identity.client_order_index,
        market_index=market_index,
        request_hash=request_hash,
    )
    db.add(binding)
    db.flush()
    return binding


def _reservation_for_action(
    db: Session,
    *,
    action_key: str,
    account_index: int,
    api_key_index: int,
) -> NonceReservation | None:
    reservation = db.scalar(
        select(NonceReservation).where(NonceReservation.replay_key == action_key)
    )
    if reservation is None:
        return None
    if (
        reservation.account_index != account_index
        or reservation.api_key_index != api_key_index
    ):
        raise LighterActionReplayMismatch(
            f"{action_key} nonce reservation belongs to a different request scope"
        )
    return reservation


class LighterOrderActions:
    """Small synchronous orchestration seam around an injected provider transport."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        transport: LighterActionTransport,
    ) -> None:
        self._session_factory = session_factory
        self._transport = transport
        self._account_index, self._api_key_index = _transport_scope(transport)

    def _prepare(
        self,
        *,
        action_key: str,
        action_type: str,
        market_index: int,
        client_order_id: str,
        request_payload: dict[str, Any],
        require_existing_identity: bool,
    ) -> tuple[OrderIdentity, int]:
        request_hash = _canonical_hash(request_payload)
        with self._session_factory() as db:
            # Keep one global advisory-lock order with the standalone SAI-069
            # seam: order identity -> action replay key -> nonce scope.
            if require_existing_identity:
                identity = _existing_identity(
                    db,
                    account_index=self._account_index,
                    client_order_id=client_order_id,
                )
            else:
                try:
                    identity = resolve_lighter_order_identity(
                        db,
                        account_index=self._account_index,
                        client_order_id=client_order_id,
                    )
                except LighterReplayError as exc:
                    raise LighterOrderActionError(str(exc)) from exc

            _lock(db, "lighter-replay-key", action_key)
            _lock(
                db,
                "lighter-nonce-scope",
                f"{self._account_index}:{self._api_key_index}",
            )

            _bind_request(
                db,
                action_key=action_key,
                action_type=action_type,
                account_index=self._account_index,
                api_key_index=self._api_key_index,
                identity=identity,
                market_index=market_index,
                request_hash=request_hash,
            )

            reservation = _reservation_for_action(
                db,
                action_key=action_key,
                account_index=self._account_index,
                api_key_index=self._api_key_index,
            )
            if reservation is not None:
                if reservation.state == "CONSUMED":
                    raise LighterActionAlreadyConsumed(
                        f"{action_key} was already consumed and will not be resubmitted"
                    )
                if reservation.state != "RESERVED":
                    raise LighterOrderActionError(
                        f"unexpected nonce reservation state {reservation.state!r}"
                    )
                nonce = reservation.nonce
            else:
                provider_next_nonce = self._transport.next_nonce()
                try:
                    reservation = reserve_lighter_nonce(
                        db,
                        account_index=self._account_index,
                        api_key_index=self._api_key_index,
                        replay_key=action_key,
                        provider_next_nonce=provider_next_nonce,
                    )
                except LighterReplayError as exc:
                    raise LighterOrderActionError(str(exc)) from exc
                nonce = reservation.nonce

            # Identity, immutable request binding and nonce ownership must be
            # durable before provider I/O begins.
            db.commit()
            return identity, nonce

    def _accept_or_raise(
        self,
        *,
        action_key: str,
        ack: LighterActionAck,
    ) -> LighterActionAck:
        if not isinstance(ack, LighterActionAck):
            raise LighterActionRejected("invalid Lighter provider acknowledgement")
        if ack.code != 200 or not isinstance(ack.tx_hash, str) or not ack.tx_hash.strip():
            raise LighterActionRejected(
                f"Lighter action rejected with code {ack.code}: {ack.message or 'no message'}"
            )

        with self._session_factory() as db:
            try:
                mark_lighter_nonce_consumed(
                    db,
                    replay_key=action_key,
                    consumed_at=datetime.now(UTC),
                )
            except LighterReplayError as exc:
                raise LighterOrderActionError(str(exc)) from exc
            db.commit()
        return ack

    def create_limit(
        self,
        *,
        market: LighterMarketFact,
        client_order_id: str,
        quantity: Decimal,
        price: Decimal,
        is_ask: bool,
        post_only: bool,
    ) -> LighterActionAck:
        base_amount, wire_price = _validate_order_values(
            market,
            quantity=quantity,
            price=price,
        )
        if not isinstance(is_ask, bool) or not isinstance(post_only, bool):
            raise LighterOrderActionError("is_ask and post_only must be booleans")

        action_key = f"CREATE:{client_order_id}"
        time_in_force = _TIF_POST_ONLY if post_only else _TIF_GTT
        request_payload = {
            "action": "CREATE",
            "market_index": market.market_id,
            "client_order_id": client_order_id,
            "base_amount": base_amount,
            "price": wire_price,
            "is_ask": is_ask,
            "order_type": _ORDER_TYPE_LIMIT,
            "time_in_force": time_in_force,
            "reduce_only": False,
            "trigger_price": _NIL_TRIGGER_PRICE,
            "order_expiry": _DEFAULT_LIMIT_EXPIRY,
        }
        identity, nonce = self._prepare(
            action_key=action_key,
            action_type="CREATE",
            market_index=market.market_id,
            client_order_id=client_order_id,
            request_payload=request_payload,
            require_existing_identity=False,
        )

        ack = self._transport.create_order(
            market_index=market.market_id,
            client_order_index=identity.client_order_index,
            base_amount=base_amount,
            price=wire_price,
            is_ask=is_ask,
            order_type=_ORDER_TYPE_LIMIT,
            time_in_force=time_in_force,
            reduce_only=False,
            trigger_price=_NIL_TRIGGER_PRICE,
            order_expiry=_DEFAULT_LIMIT_EXPIRY,
            skip_nonce=_SKIP_NONCE_ON,
            nonce=nonce,
            api_key_index=self._api_key_index,
        )
        return self._accept_or_raise(action_key=action_key, ack=ack)

    def cancel(
        self,
        *,
        market: LighterMarketFact,
        client_order_id: str,
    ) -> LighterActionAck:
        _validate_market(market)
        action_key = f"CANCEL:{client_order_id}"
        request_payload = {
            "action": "CANCEL",
            "market_index": market.market_id,
            "client_order_id": client_order_id,
        }
        identity, nonce = self._prepare(
            action_key=action_key,
            action_type="CANCEL",
            market_index=market.market_id,
            client_order_id=client_order_id,
            request_payload=request_payload,
            require_existing_identity=True,
        )
        ack = self._transport.cancel_order(
            market_index=market.market_id,
            order_index=identity.client_order_index,
            skip_nonce=_SKIP_NONCE_ON,
            nonce=nonce,
            api_key_index=self._api_key_index,
        )
        return self._accept_or_raise(action_key=action_key, ack=ack)

    def reduce_market(
        self,
        *,
        market: LighterMarketFact,
        client_order_id: str,
        quantity: Decimal,
        worst_price: Decimal,
        is_ask: bool,
    ) -> LighterActionAck:
        base_amount, wire_price = _validate_order_values(
            market,
            quantity=quantity,
            price=worst_price,
        )
        if not isinstance(is_ask, bool):
            raise LighterOrderActionError("is_ask must be a boolean")

        action_key = f"REDUCE:{client_order_id}"
        request_payload = {
            "action": "REDUCE",
            "market_index": market.market_id,
            "client_order_id": client_order_id,
            "base_amount": base_amount,
            "price": wire_price,
            "is_ask": is_ask,
            "order_type": _ORDER_TYPE_MARKET,
            "time_in_force": _TIF_IOC,
            "reduce_only": True,
            "trigger_price": _NIL_TRIGGER_PRICE,
            "order_expiry": _DEFAULT_IOC_EXPIRY,
        }
        identity, nonce = self._prepare(
            action_key=action_key,
            action_type="REDUCE",
            market_index=market.market_id,
            client_order_id=client_order_id,
            request_payload=request_payload,
            require_existing_identity=False,
        )
        ack = self._transport.create_order(
            market_index=market.market_id,
            client_order_index=identity.client_order_index,
            base_amount=base_amount,
            price=wire_price,
            is_ask=is_ask,
            order_type=_ORDER_TYPE_MARKET,
            time_in_force=_TIF_IOC,
            reduce_only=True,
            trigger_price=_NIL_TRIGGER_PRICE,
            order_expiry=_DEFAULT_IOC_EXPIRY,
            skip_nonce=_SKIP_NONCE_ON,
            nonce=nonce,
            api_key_index=self._api_key_index,
        )
        return self._accept_or_raise(action_key=action_key, ack=ack)

    def arm_position_stop(
        self,
        *,
        market: LighterMarketFact,
        client_order_id: str,
        position_side: str,
        trigger_price: Decimal,
        worst_price: Decimal,
    ) -> LighterActionAck:
        """Arm one position-tied, reduce-only STOP_LOSS_LIMIT order.

        Lighter's position-tied representation deliberately uses base_amount=0:
        the provider then keeps the stop attached to the whole current position
        as its size changes.  The limit price is an explicit adverse-slippage
        ceiling/floor, not an independent entry price.
        """

        _validate_market(market)
        if position_side not in {"LONG", "SHORT"}:
            raise LighterOrderActionError("position_side must be LONG or SHORT")

        parsed_trigger = _positive_decimal("trigger_price", trigger_price)
        parsed_worst = _positive_decimal("worst_price", worst_price)
        if position_side == "LONG":
            if parsed_worst > parsed_trigger:
                raise LighterOrderActionError(
                    "LONG protection worst_price must be <= trigger_price"
                )
            is_ask = True
        else:
            if parsed_worst < parsed_trigger:
                raise LighterOrderActionError(
                    "SHORT protection worst_price must be >= trigger_price"
                )
            is_ask = False

        wire_trigger = _scale_exact(
            "trigger_price",
            parsed_trigger,
            decimals=market.price_decimals,
            maximum=_UINT32_MAX,
        )
        wire_worst = _scale_exact(
            "worst_price",
            parsed_worst,
            decimals=market.price_decimals,
            maximum=_UINT32_MAX,
        )

        action_key = f"PROTECT:{client_order_id}"
        request_payload = {
            "action": "PROTECT",
            "market_index": market.market_id,
            "client_order_id": client_order_id,
            "position_side": position_side,
            "base_amount": 0,
            "price": wire_worst,
            "is_ask": is_ask,
            "order_type": _ORDER_TYPE_STOP_LOSS_LIMIT,
            "time_in_force": _TIF_GTT,
            "reduce_only": True,
            "trigger_price": wire_trigger,
            "order_expiry": _DEFAULT_LIMIT_EXPIRY,
        }
        identity, nonce = self._prepare(
            action_key=action_key,
            action_type="PROTECT",
            market_index=market.market_id,
            client_order_id=client_order_id,
            request_payload=request_payload,
            require_existing_identity=False,
        )
        ack = self._transport.create_order(
            market_index=market.market_id,
            client_order_index=identity.client_order_index,
            base_amount=0,
            price=wire_worst,
            is_ask=is_ask,
            order_type=_ORDER_TYPE_STOP_LOSS_LIMIT,
            time_in_force=_TIF_GTT,
            reduce_only=True,
            trigger_price=wire_trigger,
            order_expiry=_DEFAULT_LIMIT_EXPIRY,
            skip_nonce=_SKIP_NONCE_ON,
            nonce=nonce,
            api_key_index=self._api_key_index,
        )
        return self._accept_or_raise(action_key=action_key, ack=ack)


__all__ = [
    "LighterActionAck",
    "LighterActionAlreadyConsumed",
    "LighterActionRejected",
    "LighterActionReplayMismatch",
    "LighterActionTransport",
    "LighterOrderActionError",
    "LighterOrderActions",
]
