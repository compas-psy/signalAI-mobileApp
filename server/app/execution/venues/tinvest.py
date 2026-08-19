"""T-Invest futures adapter parity plus SAI-039 protection safety.

The module preserves the provider semantics extracted from the legacy Flutter
``TInvestBroker`` behind the server-side ``VenueAdapter`` contract. It has no
HTTP/token factory and is not registered by the production execution worker.

Late partial fills extend protection additively: an already-active stop is never
cancelled merely to increase coverage. Multiple exact-price stop legs are valid
only when their aggregate quantity equals the required protected quantity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import Callable, Mapping, Protocol
from uuid import NAMESPACE_URL, uuid5

from ...models.execution import ExecutionIntent, ExecutionOrder, ExecutionProtection
from ..service import (
    ExecutionFillSnapshot,
    ExecutionProtectionAck,
    ExecutionSubmitAck,
    PreSubmitReconciliation,
    ProtectionReconciliation,
    SubmissionReconciliation,
)
from .base import VenueAdapter
from .capabilities import VenueCapabilities


class TInvestProviderError(RuntimeError):
    """Structured provider/transport error with explicit not-found semantics."""

    def __init__(self, *, code: str, message: str, not_found: bool = False) -> None:
        self.code = str(code)
        self.message = str(message)
        self.is_not_found = bool(not_found)
        super().__init__(f"T-Invest {self.code}: {self.message}")

    @classmethod
    def not_found(cls, message: str = "order not found") -> "TInvestProviderError":
        return cls(code="NOT_FOUND", message=message, not_found=True)


class TInvestTransport(Protocol):
    def call(
        self,
        service: str,
        method: str,
        body: dict[str, object],
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class TInvestOrderPlan:
    account_id: str
    instrument_uid: str
    ticker: str
    long: bool
    quantity_lots: int
    entry: Decimal
    price_step: Decimal
    stop_loss: Decimal
    stop_entry: bool = False


def provider_request_id(intent: ExecutionIntent, *, purpose: str) -> str:
    """Stable provider UID36 distinct for entry, stop and emergency close."""

    intent_id = getattr(intent, "id")
    return str(uuid5(NAMESPACE_URL, f"signalai:tinvest:{intent_id.hex}:{purpose}"))


def align_price(price: Decimal, step: Decimal) -> Decimal:
    value = Decimal(price)
    increment = Decimal(step)
    if increment <= 0:
        return value
    steps = (value / increment).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return steps * increment


def decimal_to_quotation(value: Decimal) -> dict[str, object]:
    decimal = Decimal(value)
    negative = decimal < 0
    absolute = -decimal if negative else decimal
    units = int(absolute.to_integral_value(rounding=ROUND_FLOOR))
    nano = int(
        ((absolute - Decimal(units)) * Decimal(1_000_000_000)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    if nano >= 1_000_000_000:
        units += 1
        nano -= 1_000_000_000
    if negative:
        units = -units
        nano = -nano
    return {"units": str(units), "nano": nano}


def quotation_to_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, str)):
        return Decimal(str(value))
    if not isinstance(value, Mapping):
        raise TInvestProviderError(code="INVALID_RESPONSE", message="invalid quotation")
    try:
        units = Decimal(str(value.get("units", "0")))
        nano = Decimal(str(value.get("nano", 0))) / Decimal(1_000_000_000)
    except Exception as exc:
        raise TInvestProviderError(
            code="INVALID_RESPONSE", message=f"invalid quotation: {exc}"
        ) from exc
    return units + nano


def _parse_time(value: object, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return fallback


def _exact_lots(quantity: Decimal) -> int:
    value = Decimal(quantity)
    integral = value.to_integral_value()
    if value <= 0 or value != integral:
        raise TInvestProviderError(
            code="INVALID_ORDER",
            message=f"T-Invest safety quantity must be positive whole lots, got {value}",
        )
    return int(integral)


def _emergency_status(status: object) -> str:
    value = str(status or "")
    return {
        "EXECUTION_REPORT_STATUS_FILL": "FILLED",
        "EXECUTION_REPORT_STATUS_PARTIALLYFILL": "PARTIALLY_FILLED",
        "EXECUTION_REPORT_STATUS_CANCELLED": "CANCELLED",
        "EXECUTION_REPORT_STATUS_REJECTED": "REJECTED",
    }.get(value, value)


class TInvestAdapter(VenueAdapter):
    """Provider-neutral wrapper over the proven T-Invest futures semantics."""

    venue = "TINVEST"

    def __init__(
        self,
        *,
        transport: TInvestTransport,
        plan_resolver: Callable[[ExecutionIntent], TInvestOrderPlan],
        clock: Callable[[], datetime],
        sandbox: bool,
    ) -> None:
        self._transport = transport
        self._plan_resolver = plan_resolver
        self._clock = clock
        self._sandbox = bool(sandbox)

    @property
    def capabilities(self) -> VenueCapabilities:
        return VenueCapabilities(
            limit_order=True,
            stop_protection=True,
            client_order_id=True,
            min_qty_step=True,
            sandbox=True,
            reconciliation_query=True,
        )

    def _call(
        self,
        service: str,
        method: str,
        body: dict[str, object],
    ) -> Mapping[str, object]:
        try:
            response = self._transport.call(service, method, body)
        except TInvestProviderError:
            raise
        except Exception as exc:
            raise TInvestProviderError(
                code="TRANSPORT", message=str(exc) or exc.__class__.__name__
            ) from exc
        if isinstance(response, TInvestProviderError):
            raise response
        if not isinstance(response, Mapping):
            raise TInvestProviderError(
                code="INVALID_RESPONSE", message="provider response is not an object"
            )
        return response

    def _order_service(self) -> tuple[str, str, str]:
        if self._sandbox:
            return "SandboxService", "PostSandboxOrder", "GetSandboxOrderState"
        return "OrdersService", "PostOrder", "GetOrderState"

    def _stop_service(self) -> tuple[str, str, str]:
        if self._sandbox:
            return "SandboxService", "PostSandboxStopOrder", "GetSandboxStopOrders"
        return "StopOrdersService", "PostStopOrder", "GetStopOrders"

    def _get_order_state(
        self,
        intent: ExecutionIntent,
        *,
        provider_order_id: str | None = None,
        request_purpose: str = "entry",
    ) -> Mapping[str, object]:
        plan = self._plan_resolver(intent)
        service, _, get_method = self._order_service()
        if provider_order_id:
            body: dict[str, object] = {
                "accountId": plan.account_id,
                "orderId": provider_order_id,
            }
        else:
            body = {
                "accountId": plan.account_id,
                "orderId": provider_request_id(intent, purpose=request_purpose),
                "orderIdType": "ORDER_ID_TYPE_REQUEST",
            }
        return self._call(service, get_method, body)

    def _active_stop_orders(self, plan: TInvestOrderPlan) -> list[Mapping[str, object]]:
        service, _, get_method = self._stop_service()
        response = self._call(
            service,
            get_method,
            {
                "accountId": plan.account_id,
                "status": "STOP_ORDER_STATUS_ACTIVE",
            },
        )
        raw_orders = response.get("stopOrders")
        if not isinstance(raw_orders, list):
            raise TInvestProviderError(
                code="INVALID_RESPONSE",
                message="T-Invest active-stop response lacks stopOrders",
            )
        return [raw for raw in raw_orders if isinstance(raw, Mapping)]

    def _matching_stop_legs(
        self,
        plan: TInvestOrderPlan,
        *,
        stop_price: Decimal,
        raw_orders: list[Mapping[str, object]],
    ) -> list[tuple[str, Decimal]]:
        expected_direction = (
            "STOP_ORDER_DIRECTION_SELL"
            if plan.long
            else "STOP_ORDER_DIRECTION_BUY"
        )
        expected_stop_price = Decimal(stop_price)
        legs: list[tuple[str, Decimal]] = []
        for item in raw_orders:
            if str(item.get("instrumentUid") or "") != plan.instrument_uid:
                continue
            if str(item.get("direction") or "") != expected_direction:
                continue
            order_type = str(
                item.get("orderType") or item.get("stopOrderType") or ""
            )
            if order_type != "STOP_ORDER_TYPE_STOP_LOSS":
                continue
            stop_id = str(item.get("stopOrderId") or "")
            if not stop_id:
                raise TInvestProviderError(
                    code="INVALID_RESPONSE",
                    message="active T-Invest stop lacks stopOrderId",
                )
            try:
                quantity = Decimal(
                    str(item.get("lotsRequested", item.get("quantity", "0")))
                )
                actual_stop_price = quotation_to_decimal(item.get("stopPrice"))
            except Exception as exc:
                if isinstance(exc, TInvestProviderError):
                    raise
                raise TInvestProviderError(
                    code="INVALID_RESPONSE",
                    message=f"invalid T-Invest stop snapshot: {exc}",
                ) from exc
            if quantity <= 0:
                raise TInvestProviderError(
                    code="INVALID_RESPONSE",
                    message="active T-Invest stop has non-positive quantity",
                )
            if actual_stop_price == expected_stop_price:
                legs.append((stop_id, quantity))
        return legs

    def reconcile_before_submit(
        self,
        intent: ExecutionIntent,
    ) -> PreSubmitReconciliation:
        try:
            state = self._get_order_state(intent)
        except TInvestProviderError as exc:
            if exc.is_not_found:
                return PreSubmitReconciliation.absent()
            return PreSubmitReconciliation.unknown(str(exc))

        provider_order_id = str(state.get("orderId") or "")
        status = str(state.get("executionReportStatus") or "")
        if not provider_order_id:
            return PreSubmitReconciliation.unknown(
                "T-Invest order-state lookup returned no exchange order id"
            )
        return PreSubmitReconciliation.unknown(
            "provider order already exists for "
            f"{provider_request_id(intent, purpose='entry')}: "
            f"{provider_order_id}{f' ({status})' if status else ''}"
        )

    def reconcile_submission(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
    ) -> SubmissionReconciliation:
        del order
        try:
            state = self._get_order_state(intent)
        except TInvestProviderError as exc:
            if exc.is_not_found:
                return SubmissionReconciliation.absent()
            return SubmissionReconciliation.unknown(str(exc))

        provider_order_id = str(state.get("orderId") or "")
        status = str(state.get("executionReportStatus") or "")
        if not provider_order_id or not status:
            return SubmissionReconciliation.unknown(
                "T-Invest reconciliation row lacks orderId/executionReportStatus"
            )
        return SubmissionReconciliation.found(
            provider_order_id=provider_order_id,
            status=status,
            acknowledged_at=_parse_time(state.get("orderDate"), self._clock()),
        )

    def submit(
        self,
        intent: ExecutionIntent,
        *,
        client_order_id: str,
    ) -> ExecutionSubmitAck:
        del client_order_id
        plan = self._plan_resolver(intent)
        if plan.stop_entry:
            raise TInvestProviderError(
                code="UNSUPPORTED",
                message="stop-entry is not implemented for T-Invest parity path",
            )
        if plan.quantity_lots < 1:
            raise TInvestProviderError(
                code="INVALID_ORDER", message="quantity is below one lot"
            )

        service, post_method, _ = self._order_service()
        body: dict[str, object] = {
            "accountId": plan.account_id,
            "instrumentId": plan.instrument_uid,
            "quantity": str(plan.quantity_lots),
            "price": decimal_to_quotation(align_price(plan.entry, plan.price_step)),
            "direction": (
                "ORDER_DIRECTION_BUY" if plan.long else "ORDER_DIRECTION_SELL"
            ),
            "orderType": "ORDER_TYPE_LIMIT",
            "orderId": provider_request_id(intent, purpose="entry"),
        }
        if self._sandbox:
            body.update(
                {
                    "timeInForce": "TIME_IN_FORCE_DAY",
                    "priceType": "PRICE_TYPE_POINT",
                    "confirmMarginTrade": True,
                }
            )

        response = self._call(service, post_method, body)
        provider_order_id = str(response.get("orderId") or "")
        if not provider_order_id:
            raise TInvestProviderError(
                code="INVALID_RESPONSE",
                message="successful order submission lacks exchange orderId",
            )
        return ExecutionSubmitAck(
            provider_order_id=provider_order_id,
            status="ACKNOWLEDGED",
            acknowledged_at=self._clock(),
        )

    def consume_fills(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
    ) -> tuple[ExecutionFillSnapshot, ...]:
        provider_order_id = getattr(order, "provider_order_id", None)
        if not provider_order_id:
            return ()
        state = self._get_order_state(
            intent,
            provider_order_id=str(provider_order_id),
        )
        raw_stages = state.get("stages")
        if not isinstance(raw_stages, list):
            return ()
        currency = str(state.get("currency") or "").upper() or None

        snapshots: list[ExecutionFillSnapshot] = []
        for raw in raw_stages:
            if not isinstance(raw, Mapping):
                raise TInvestProviderError(
                    code="INVALID_RESPONSE", message="invalid execution stage"
                )
            provider_fill_id = str(raw.get("tradeId") or "")
            try:
                quantity = Decimal(str(raw.get("quantity")))
                price = quotation_to_decimal(raw.get("price"))
            except Exception as exc:
                if isinstance(exc, TInvestProviderError):
                    raise
                raise TInvestProviderError(
                    code="INVALID_RESPONSE", message=f"invalid execution stage: {exc}"
                ) from exc
            if not provider_fill_id or quantity <= 0 or price <= 0:
                raise TInvestProviderError(
                    code="INVALID_RESPONSE",
                    message="execution stage lacks tradeId/positive quantity/price",
                )
            snapshots.append(
                ExecutionFillSnapshot(
                    provider_fill_id=provider_fill_id,
                    quantity=quantity,
                    price=price,
                    fee_amount=Decimal("0"),
                    fee_currency=currency,
                    filled_at=_parse_time(raw.get("executionTime"), self._clock()),
                )
            )
        return tuple(snapshots)

    def arm_protection(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
        *,
        filled_quantity: Decimal,
    ) -> ExecutionProtectionAck:
        del order
        plan = self._plan_resolver(intent)
        target_lots = _exact_lots(filled_quantity)
        current_lots = 0

        # Preserve the original one-lot initial call shape. Larger targets may
        # represent either a full initial fill or an expansion; authoritative
        # active-stop read-back distinguishes those cases without cancellation.
        if target_lots > 1:
            raw_orders = self._active_stop_orders(plan)
            legs = self._matching_stop_legs(
                plan,
                stop_price=align_price(plan.stop_loss, plan.price_step),
                raw_orders=raw_orders,
            )
            current_quantity = sum((quantity for _, quantity in legs), Decimal("0"))
            current_lots = _exact_lots(current_quantity) if current_quantity > 0 else 0
            if current_lots > target_lots:
                raise TInvestProviderError(
                    code="PROTECTION_OVER_COVERED",
                    message=(
                        "active T-Invest stop coverage exceeds required quantity: "
                        f"{current_lots} > {target_lots}"
                    ),
                )
            if current_lots == target_lots:
                preferred_id = sorted(stop_id for stop_id, _ in legs)[-1]
                return ExecutionProtectionAck(
                    provider_order_id=preferred_id,
                    status="ACTIVE",
                    armed_at=self._clock(),
                )

        delta_lots = target_lots - current_lots
        service, post_method, _ = self._stop_service()
        body: dict[str, object] = {
            "accountId": plan.account_id,
            "instrumentId": plan.instrument_uid,
            "quantity": str(delta_lots),
            "stopPrice": decimal_to_quotation(
                align_price(plan.stop_loss, plan.price_step)
            ),
            "direction": (
                "STOP_ORDER_DIRECTION_SELL"
                if plan.long
                else "STOP_ORDER_DIRECTION_BUY"
            ),
            "stopOrderType": "STOP_ORDER_TYPE_STOP_LOSS",
            "expirationType": "STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL",
        }
        if self._sandbox:
            purpose = "stop" if current_lots == 0 else f"stop-total-{target_lots}"
            body.update(
                {
                    "orderId": provider_request_id(intent, purpose=purpose),
                    "priceType": "PRICE_TYPE_POINT",
                    "confirmMarginTrade": True,
                }
            )
        response = self._call(service, post_method, body)
        provider_stop_id = str(response.get("stopOrderId") or "")
        if not provider_stop_id:
            raise TInvestProviderError(
                code="INVALID_RESPONSE",
                message="successful protective stop lacks stopOrderId",
            )
        return ExecutionProtectionAck(
            provider_order_id=provider_stop_id,
            status="ACTIVE",
            armed_at=self._clock(),
        )

    def reconcile_protection(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
        protection: ExecutionProtection,
    ) -> ProtectionReconciliation:
        del order
        plan = self._plan_resolver(intent)
        try:
            raw_orders = self._active_stop_orders(plan)
            legs = self._matching_stop_legs(
                plan,
                stop_price=Decimal(protection.stop_price),
                raw_orders=raw_orders,
            )
        except TInvestProviderError as exc:
            return ProtectionReconciliation.unknown(str(exc))

        expected_quantity = Decimal(protection.quantity)
        if not legs:
            return ProtectionReconciliation.missing(
                "T-Invest exact-price protective stop coverage is not active"
            )

        aggregate_quantity = sum((quantity for _, quantity in legs), Decimal("0"))
        if aggregate_quantity < expected_quantity:
            return ProtectionReconciliation.missing(
                "T-Invest active protective stop coverage is below required quantity: "
                f"{aggregate_quantity} < {expected_quantity}"
            )
        if aggregate_quantity > expected_quantity:
            return ProtectionReconciliation.unknown(
                "T-Invest active protective stop coverage exceeds required quantity: "
                f"{aggregate_quantity} > {expected_quantity}"
            )

        known_provider_id = str(protection.provider_order_id or "")
        leg_ids = [stop_id for stop_id, _ in legs]
        if known_provider_id and known_provider_id in leg_ids:
            provider_order_id = known_provider_id
        elif len(leg_ids) == 1:
            provider_order_id = leg_ids[0]
        else:
            # Multiple legs are expected after a deliberate expansion. With no
            # durable latest-leg id we cannot guess which ACK was lost.
            return ProtectionReconciliation.unknown(
                "multiple exact active T-Invest stop legs match aggregate coverage but no provider id is anchored"
            )

        return ProtectionReconciliation.matched(
            provider_order_id=provider_order_id,
            status="ACTIVE",
            quantity=aggregate_quantity,
            stop_price=Decimal(protection.stop_price),
            reconciled_at=self._clock(),
        )

    def emergency_flatten(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
        *,
        filled_quantity: Decimal,
        client_order_id: str,
    ) -> ExecutionSubmitAck:
        del order, client_order_id
        plan = self._plan_resolver(intent)
        lots = _exact_lots(filled_quantity)
        service, post_method, _ = self._order_service()
        body: dict[str, object] = {
            "accountId": plan.account_id,
            "instrumentId": plan.instrument_uid,
            "quantity": str(lots),
            "direction": (
                "ORDER_DIRECTION_SELL" if plan.long else "ORDER_DIRECTION_BUY"
            ),
            "orderType": "ORDER_TYPE_MARKET",
            "orderId": provider_request_id(intent, purpose="emergency-flatten"),
        }
        if self._sandbox:
            body.update(
                {
                    "priceType": "PRICE_TYPE_POINT",
                    "confirmMarginTrade": True,
                }
            )
        response = self._call(service, post_method, body)
        provider_order_id = str(response.get("orderId") or "")
        if not provider_order_id:
            raise TInvestProviderError(
                code="INVALID_RESPONSE",
                message="successful emergency close lacks exchange orderId",
            )
        return ExecutionSubmitAck(
            provider_order_id=provider_order_id,
            status="ACKNOWLEDGED",
            acknowledged_at=self._clock(),
        )

    def reconcile_emergency_flatten(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
    ) -> SubmissionReconciliation:
        del order
        try:
            state = self._get_order_state(
                intent,
                request_purpose="emergency-flatten",
            )
        except TInvestProviderError as exc:
            if exc.is_not_found:
                return SubmissionReconciliation.absent()
            return SubmissionReconciliation.unknown(str(exc))

        provider_order_id = str(state.get("orderId") or "")
        status = _emergency_status(state.get("executionReportStatus"))
        if not provider_order_id or not status:
            return SubmissionReconciliation.unknown(
                "T-Invest emergency reconciliation lacks orderId/executionReportStatus"
            )
        return SubmissionReconciliation.found(
            provider_order_id=provider_order_id,
            status=status,
            acknowledged_at=_parse_time(state.get("orderDate"), self._clock()),
        )

    def reconcile(self, intent: ExecutionIntent) -> None:
        del intent

    def manage_until_close(self, intent: ExecutionIntent) -> None:
        del intent


__all__ = [
    "TInvestAdapter",
    "TInvestOrderPlan",
    "TInvestProviderError",
    "TInvestTransport",
    "align_price",
    "decimal_to_quotation",
    "provider_request_id",
    "quotation_to_decimal",
]
