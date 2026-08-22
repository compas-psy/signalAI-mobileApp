"""Server-local SAI-077 Lighter testnet smoke command.

Credentials are never accepted by CLI. They are loaded only from the encrypted
server-side ``lighter_testnet_trade`` slot inside the operator boundary.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Sequence

from .db import get_session_factory
from .execution.venues.lighter_facts import LighterMarketFact
from .execution.venues.lighter_testnet_operator import (
    LighterTestnetOperatorError,
    recover_lighter_testnet_operator_cancel,
    run_lighter_testnet_operator_smoke,
)

_REQUEST_SCHEMA = "signalai.lighter.testnet-smoke-request.v1"
_REQUEST_FIELDS = {"schema", "client_order_id", "quantity", "price", "is_ask", "market"}
_MARKET_FIELDS = {
    "market_id", "symbol", "status", "min_base_amount", "min_quote_amount",
    "size_decimals", "price_decimals", "quote_decimals", "maker_fee_pct",
    "taker_fee_pct", "liquidation_fee_pct", "order_quote_limit", "multiplier",
    "observed_at",
}


class LighterSmokeCliError(ValueError):
    """Sanitized invalid operator-input error."""


@dataclass(frozen=True, slots=True)
class LighterSmokeRequest:
    client_order_id: str
    quantity: Decimal
    price: Decimal
    is_ask: bool
    market: LighterMarketFact


def _finite_decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise LighterSmokeCliError(f"{field} must be a finite decimal") from None
    if not result.is_finite():
        raise LighterSmokeCliError(f"{field} must be a finite decimal")
    return result


def _positive_decimal(value: object, field: str) -> Decimal:
    result = _finite_decimal(value, field)
    if result <= 0:
        raise LighterSmokeCliError(f"{field} must be positive")
    return result


def _nonnegative_decimal(value: object, field: str) -> Decimal:
    result = _finite_decimal(value, field)
    if result < 0:
        raise LighterSmokeCliError(f"{field} must not be negative")
    return result


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise LighterSmokeCliError(f"{field} must be an integer >= {minimum}")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LighterSmokeCliError(f"{field} must be non-blank")
    return value.strip()


def _time(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise LighterSmokeCliError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LighterSmokeCliError(f"{field} must be an ISO-8601 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LighterSmokeCliError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def load_smoke_request(path: str | Path) -> LighterSmokeRequest:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise LighterSmokeCliError("request cannot be loaded") from None
    if not isinstance(document, dict):
        raise LighterSmokeCliError("request must be an object")
    if set(document) != _REQUEST_FIELDS:
        raise LighterSmokeCliError("request fields do not match the safe contract")
    if document.get("schema") != _REQUEST_SCHEMA:
        raise LighterSmokeCliError("request schema is unsupported")
    market_raw = document.get("market")
    if not isinstance(market_raw, dict) or set(market_raw) != _MARKET_FIELDS:
        raise LighterSmokeCliError("market request fields do not match the safe contract")
    is_ask = document.get("is_ask")
    if not isinstance(is_ask, bool):
        raise LighterSmokeCliError("is_ask must be bool")
    market = LighterMarketFact(
        market_id=_integer(market_raw.get("market_id"), "market.market_id"),
        symbol=_text(market_raw.get("symbol"), "market.symbol"),
        status=_text(market_raw.get("status"), "market.status"),
        min_base_amount=_positive_decimal(market_raw.get("min_base_amount"), "market.min_base_amount"),
        min_quote_amount=_positive_decimal(market_raw.get("min_quote_amount"), "market.min_quote_amount"),
        size_decimals=_integer(market_raw.get("size_decimals"), "market.size_decimals"),
        price_decimals=_integer(market_raw.get("price_decimals"), "market.price_decimals"),
        quote_decimals=_integer(market_raw.get("quote_decimals"), "market.quote_decimals"),
        maker_fee_pct=_nonnegative_decimal(market_raw.get("maker_fee_pct"), "market.maker_fee_pct"),
        taker_fee_pct=_nonnegative_decimal(market_raw.get("taker_fee_pct"), "market.taker_fee_pct"),
        liquidation_fee_pct=_nonnegative_decimal(market_raw.get("liquidation_fee_pct"), "market.liquidation_fee_pct"),
        order_quote_limit=_positive_decimal(market_raw.get("order_quote_limit"), "market.order_quote_limit"),
        multiplier=_positive_decimal(market_raw.get("multiplier"), "market.multiplier"),
        observed_at=_time(market_raw.get("observed_at"), "market.observed_at"),
    )
    return LighterSmokeRequest(
        client_order_id=_text(document.get("client_order_id"), "client_order_id"),
        quantity=_positive_decimal(document.get("quantity"), "quantity"),
        price=_positive_decimal(document.get("price"), "price"),
        is_ask=is_ask,
        market=market,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one evidence-gated Lighter testnet create/cancel smoke")
    parser.add_argument("--shadow-evidence", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--result-evidence", required=True)
    parser.add_argument("--recover-cancel", action="store_true")
    return parser


def _summary(result) -> dict[str, object]:
    return {
        "status": result.status,
        "evidence_sha256": result.evidence_sha256,
        "create_tx_hash": result.create_tx_hash,
        "cancel_tx_hash": result.cancel_tx_hash,
        "eligible_for_live": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        request = load_smoke_request(args.request)
        factory = get_session_factory()
        db = factory()
        try:
            common = {
                "db": db,
                "session_factory": factory,
                "shadow_evidence_path": args.shadow_evidence,
                "result_evidence_path": args.result_evidence,
                "market": request.market,
                "observed_at": datetime.now(UTC),
            }
            if args.recover_cancel:
                result = recover_lighter_testnet_operator_cancel(**common)
            else:
                result = run_lighter_testnet_operator_smoke(
                    **common,
                    client_order_id=request.client_order_id,
                    quantity=request.quantity,
                    price=request.price,
                    is_ask=request.is_ask,
                )
        finally:
            db.close()
    except (LighterSmokeCliError, LighterTestnetOperatorError):
        print(json.dumps({"status": "BLOCKED", "eligible_for_live": False}, sort_keys=True))
        return 1
    print(json.dumps(_summary(result), sort_keys=True))
    if result.status == "SUCCESS":
        return 0
    if result.status == "CANCEL_FAILED":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
