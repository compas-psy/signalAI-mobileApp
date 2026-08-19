"""T-Invest futures adapter parity plus SAI-039 protection safety.

The module preserves the provider semantics extracted from the legacy Flutter
``TInvestBroker`` behind the server-side ``VenueAdapter`` contract. It has no
HTTP/token factory and is not registered by the production execution worker.
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
        lots = _exact_lots(filled_quantity)
        if self._sandbox:
            service = "SandboxService"
            method = "PostSandboxStopOrder"
        else:
            service = "StopOrdersService"
            method = "PostStopOrder"

        body: dict[str, object] = {
            "accountId": plan.account_id,
            "instrumentId": plan.instrument_uid,
            "quantity": str(lots),
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
            body.update(
                {
                    "orderId": provider_request_id(intent, purpose="stop"),
                    "priceType": "PRICE_TYPE_POINT",
                    "confirmMarginTrade": True,
                }
            )
        response = self._call(service, method, body)
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
        service = "SandboxService" if self._sandbox else "StopOrdersService"
        method = "GetSandboxStopOrders" if self._sandbox else "GetStopOrders"
        try:
            response = self._call(
                service,
                method,
                {
                    "accountId": plan.account_id,
                    "status": "STOP_ORDER_STATUS_ACTIVE",
                },
            )
        except TInvestProviderError as exc:
            return ProtectionReconciliation.unknown(str(exc))

        raw_orders = response.get("stopOrders")
        if not isinstance(raw_orders, list):
            return ProtectionReconciliation.unknown(
                "T-Invest active-stop response lacks stopOrders"
            )

        expected_direction = (
            "STOP_ORDER_DIRECTION_SELL"
            if plan.long
            else "STOP_ORDER_DIRECTION_BUY"
        )
        expected_quantity = Decimal(protection.quantity)
        expected_stop_price = Decimal(protection.stop_price)
        provider_order_id = str(protection.provider_order_id or "")

        def parse_exact_candidate(
            item: Mapping[str, object],
        ) -> tuple[str, Decimal, Decimal] | None:
            if str(item.get("instrumentUid") or "") != plan.instrument_uid:
                return None
            if str(item.get("direction") or "") != expected_direction:
                return None
            order_type = str(
                item.get("orderType") or item.get("stopOrderType") or ""
            )
            if order_type != "STOP_ORDER_TYPE_STOP_LOSS":
                return None
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
                stop_price = quotation_to_decimal(item.get("stopPrice"))
            except Exception as exc:
                if isinstance(exc, TInvestProviderError):
                    raise
                raise TInvestProviderError(
                    code="INVALID_RESPONSE",
                    message=f"invalid T-Invest stop snapshot: {exc}",
                ) from exc
            if quantity != expected_quantity or stop_price != expected_stop_price:
                return None
            return stop_id, quantity, stop_price

        if provider_order_id:
            row = next(
                (
                    item
                    for item in raw_orders
                    if isinstance(item, Mapping)
                    and str(item.get("stopOrderId") or "") == provider_order_id
                ),
                None,
            )
            if row is None:
                return ProtectionReconciliation.missing(
                    "T-Invest protective stop is not active"
                )
            try:
                exact = parse_exact_candidate(row)
            except TInvestProviderError as exc:
                return ProtectionReconciliation.unknown(str(exc))
            if exact is None:
                return ProtectionReconciliation.missing(
                    "T-Invest active stop does not exactly match instrument, direction, quantity and price"
                )
            stop_id, quantity, stop_price = exact
            return ProtectionReconciliation.matched(
                provider_order_id=stop_id,
                status="ACTIVE",
                quantity=quantity,
                stop_price=stop_price,
                reconciled_at=self._clock(),
            )

        exact_candidates: list[tuple[str, Decimal, Decimal]] = []
        for raw in raw_orders:
            if not isinstance(raw, Mapping):
                continue
            try:
                candidate = parse_exact_candidate(raw)
            except TInvestProviderError as exc:
                return ProtectionReconciliation.unknown(str(exc))
            if candidate is not None:
                exact_candidates.append(candidate)

        if not exact_candidates:
            return ProtectionReconciliation.missing(
                "T-Invest exact protective stop is not active"
            )
        if len(exact_candidates) > 1:
            return ProtectionReconciliation.unknown(
                "multiple exact active T-Invest stops match the lost acknowledgement"
            )

        stop_id, quantity, stop_price = exact_candidates[0]
        return ProtectionReconciliation.matched(
            provider_order_id=stop_id,
            status="ACTIVE",
            quantity=quantity,
            stop_price=stop_price,
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
