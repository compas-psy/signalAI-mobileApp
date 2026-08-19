"""Bybit V5 adapter parity layer for SAI-037.

This module extracts the provider semantics already proven by the legacy
Flutter ``BybitBroker`` behind the server-side ``VenueAdapter`` interface.
It deliberately has no production HTTP factory and no credential lookup: a
transport and an execution-plan resolver must be injected by the caller.
Consequently adding this module cannot enable real-money execution by itself.

SAI-039 owns the final protection-first worker wiring. Until then the existing
Flutter broker remains the rollback/parity reference and the server production
worker remains fail-closed.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Callable, Mapping, Protocol

from ...models.execution import ExecutionIntent, ExecutionOrder
from ..service import (
    ExecutionFillSnapshot,
    ExecutionProtectionAck,
    ExecutionSubmitAck,
    PreSubmitReconciliation,
    SubmissionReconciliation,
)
from .base import VenueAdapter
from .capabilities import VenueCapabilities


_BYBIT_CATEGORY = "linear"
_RECV_WINDOW = "5000"


class BybitProviderError(RuntimeError):
    """Structured provider rejection with the original Bybit code/message."""

    def __init__(self, code: int, message: str) -> None:
        self.code = int(code)
        self.message = str(message)
        super().__init__(f"Bybit {self.code}: {self.message}")


class BybitTransport(Protocol):
    """Injected provider transport.

    SAI-037 intentionally does not create a network implementation. The future
    server-side credential boundary can supply an authenticated implementation
    without changing the adapter's economic/reconciliation semantics.
    """

    def get(self, path: str, query: dict[str, str]) -> Mapping[str, object]: ...

    def post(self, path: str, body: dict[str, object]) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class BybitOrderPlan:
    """Provider-facing order plan matching the existing Flutter contract."""

    symbol: str
    long: bool
    quantity: Decimal
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    stop_entry: bool = False


@dataclass(frozen=True)
class BybitV5HmacAuth:
    """Pure V5 HMAC signer matching the legacy mobile payload contract."""

    api_key: str
    api_secret: str
    recv_window: str = _RECV_WINDOW

    def headers(self, *, timestamp_ms: int, payload: str) -> dict[str, str]:
        timestamp = str(int(timestamp_ms))
        signing_payload = f"{timestamp}{self.api_key}{self.recv_window}{payload}"
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            signing_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": self.recv_window,
            "X-BAPI-SIGN": signature,
        }


def _decimal_text(value: Decimal) -> str:
    """Serialize Decimal without exponent or insignificant trailing zeroes."""

    value = Decimal(value)
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def legacy_order_body(plan: BybitOrderPlan) -> dict[str, object]:
    """Build the same economic V5 order fields as the existing Flutter broker.

    ``orderLinkId`` is intentionally not part of this legacy parity function;
    the server adapter appends it as transport/reconciliation identity without
    changing price, quantity, side or protection economics.
    """

    body: dict[str, object] = {
        "category": _BYBIT_CATEGORY,
        "symbol": plan.symbol,
        "side": "Buy" if plan.long else "Sell",
        "orderType": "Limit",
        "qty": _decimal_text(plan.quantity),
        "price": _decimal_text(plan.entry),
        "timeInForce": "GTC",
        "stopLoss": _decimal_text(plan.stop_loss),
        "takeProfit": _decimal_text(plan.take_profit),
        "tpslMode": "Full",
        "positionIdx": 0,
    }
    if plan.stop_entry:
        body.update(
            {
                "triggerPrice": _decimal_text(plan.entry),
                "triggerDirection": 1 if plan.long else 2,
                "triggerBy": "LastPrice",
            }
        )
    return body


def _provider_result(response: Mapping[str, object]) -> Mapping[str, object]:
    raw_code = response.get("retCode", -1)
    try:
        code = int(raw_code)
    except (TypeError, ValueError):
        code = -1
    if code != 0:
        raise BybitProviderError(code, str(response.get("retMsg") or "provider error"))
    result = response.get("result")
    return result if isinstance(result, Mapping) else {}


def _items(result: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw = result.get("list")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _millis_timestamp(value: object, fallback: datetime) -> datetime:
    try:
        milliseconds = int(str(value))
    except (TypeError, ValueError):
        return fallback
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)


def _deterministic_client_order_id(intent: ExecutionIntent) -> str:
    return f"e-{intent.id.hex}"


class BybitAdapter(VenueAdapter):
    """Provider-neutral wrapper over the current Bybit V5 semantics.

    The adapter is intentionally not instantiated by production code in this
    slice. The injected plan resolver keeps side/TP/provider intent outside the
    provider layer; SAI-039 will wire that plan to the durable execution core
    when protection-first semantics are complete.
    """

    venue = "BYBIT"

    def __init__(
        self,
        *,
        transport: BybitTransport,
        plan_resolver: Callable[[ExecutionIntent], BybitOrderPlan],
        clock: Callable[[], datetime],
    ) -> None:
        self._transport = transport
        self._plan_resolver = plan_resolver
        self._clock = clock

    @property
    def capabilities(self) -> VenueCapabilities:
        # Only capabilities already represented by the extracted provider path
        # are claimed. Websocket/reduce-only/market/cancel-replace are left
        # false until their dedicated server-side implementations exist.
        return VenueCapabilities(
            limit_order=True,
            stop_protection=True,
            client_order_id=True,
            min_qty_step=True,
            sandbox=True,
            reconciliation_query=True,
        )

    def reconcile_before_submit(
        self,
        intent: ExecutionIntent,
    ) -> PreSubmitReconciliation:
        client_order_id = _deterministic_client_order_id(intent)
        try:
            result = _provider_result(
                self._transport.get(
                    "/v5/order/realtime",
                    {
                        "category": _BYBIT_CATEGORY,
                        "orderLinkId": client_order_id,
                    },
                )
            )
        except BybitProviderError as exc:
            return PreSubmitReconciliation.unknown(str(exc))

        orders = _items(result)
        if not orders:
            return PreSubmitReconciliation.absent()
        provider_id = str(orders[0].get("orderId") or "unknown")
        return PreSubmitReconciliation.unknown(
            f"provider order already exists for {client_order_id}: {provider_id}"
        )

    def reconcile_submission(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
    ) -> SubmissionReconciliation:
        try:
            result = _provider_result(
                self._transport.get(
                    "/v5/order/realtime",
                    {
                        "category": _BYBIT_CATEGORY,
                        "orderLinkId": order.client_order_id,
                    },
                )
            )
        except BybitProviderError as exc:
            return SubmissionReconciliation.unknown(str(exc))

        orders = _items(result)
        if not orders:
            return SubmissionReconciliation.absent()
        provider = orders[0]
        provider_order_id = str(provider.get("orderId") or "")
        status = str(provider.get("orderStatus") or "")
        if not provider_order_id or not status:
            return SubmissionReconciliation.unknown(
                "Bybit reconciliation row lacks orderId/orderStatus"
            )
        acknowledged_at = _millis_timestamp(
            provider.get("updatedTime"),
            self._clock(),
        )
        return SubmissionReconciliation.found(
            provider_order_id=provider_order_id,
            status=status,
            acknowledged_at=acknowledged_at,
        )

    def submit(
        self,
        intent: ExecutionIntent,
        *,
        client_order_id: str,
    ) -> ExecutionSubmitAck:
        plan = self._plan_resolver(intent)
        body = legacy_order_body(plan)
        body["orderLinkId"] = client_order_id
        result = _provider_result(self._transport.post("/v5/order/create", body))
        provider_order_id = str(result.get("orderId") or "")
        if not provider_order_id:
            raise BybitProviderError(-1, "successful create-order lacks orderId")
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
        if not order.provider_order_id:
            return ()
        result = _provider_result(
            self._transport.get(
                "/v5/execution/list",
                {
                    "category": _BYBIT_CATEGORY,
                    "orderId": order.provider_order_id,
                },
            )
        )
        snapshots: list[ExecutionFillSnapshot] = []
        for item in _items(result):
            provider_fill_id = str(item.get("execId") or "")
            try:
                quantity = Decimal(str(item.get("execQty")))
                price = Decimal(str(item.get("execPrice")))
                fee_amount = Decimal(str(item.get("execFee") or "0"))
            except Exception as exc:  # Decimal raises several parse subclasses.
                raise BybitProviderError(-1, f"invalid execution row: {exc}") from exc
            if not provider_fill_id or quantity <= 0 or price <= 0:
                raise BybitProviderError(-1, "execution row lacks id/positive qty/price")
            snapshots.append(
                ExecutionFillSnapshot(
                    provider_fill_id=provider_fill_id,
                    quantity=quantity,
                    price=price,
                    fee_amount=fee_amount,
                    fee_currency=(
                        str(item.get("feeCurrency"))
                        if item.get("feeCurrency") not in (None, "")
                        else None
                    ),
                    filled_at=_millis_timestamp(item.get("execTime"), self._clock()),
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
        del order, filled_quantity  # Bybit Full position stop is position-scoped.
        plan = self._plan_resolver(intent)
        _provider_result(
            self._transport.post(
                "/v5/position/trading-stop",
                {
                    "category": _BYBIT_CATEGORY,
                    "symbol": plan.symbol,
                    "stopLoss": _decimal_text(plan.stop_loss),
                    "tpslMode": "Full",
                    "positionIdx": 0,
                },
            )
        )
        # The legacy endpoint returns no stable protection-order id. Persist a
        # deterministic provider reference until SAI-039 reconciles the actual
        # position/protection state explicitly.
        return ExecutionProtectionAck(
            provider_order_id=f"bybit-position-stop:{plan.symbol}",
            status="ACTIVE",
            armed_at=self._clock(),
        )

    def reconcile(self, intent: ExecutionIntent) -> None:
        # Submission reconciliation is implemented above. Position/protection
        # reconciliation is intentionally owned by SAI-039/040 and the adapter
        # is not production-wired before then.
        del intent

    def manage_until_close(self, intent: ExecutionIntent) -> None:
        # Exit management is not invented in this parity slice.
        del intent


__all__ = [
    "BybitAdapter",
    "BybitOrderPlan",
    "BybitProviderError",
    "BybitTransport",
    "BybitV5HmacAuth",
    "legacy_order_body",
]
