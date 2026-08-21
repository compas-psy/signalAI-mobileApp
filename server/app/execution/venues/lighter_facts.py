"""Pure read-side normalization for Lighter market/account/margin facts.

SAI-067 intentionally contains no transport, SDK, credential, persistence or
execution behavior.  Raw provider payloads are converted into immutable,
Decimal-safe facts with explicit observation provenance.  Critical malformed
values fail closed instead of being silently coerced to zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


class LighterFactsError(ValueError):
    """Raised when a provider payload cannot be represented safely."""


def _observed_at(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LighterFactsError("observed_at must be timezone-aware")
    return value.astimezone(UTC)


def _decimal(payload: Mapping[str, Any], field: str) -> Decimal:
    raw = payload.get(field)
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise LighterFactsError(f"invalid {field}: {raw!r}") from exc
    if not value.is_finite():
        raise LighterFactsError(f"invalid {field}: {raw!r}")
    return value


def _optional_decimal(payload: Mapping[str, Any], field: str) -> Decimal | None:
    raw = payload.get(field)
    if raw in (None, ""):
        return None
    return _decimal(payload, field)


def _int(payload: Mapping[str, Any], field: str) -> int:
    raw = payload.get(field)
    if isinstance(raw, bool):
        raise LighterFactsError(f"invalid {field}: {raw!r}")
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LighterFactsError(f"invalid {field}: {raw!r}") from exc
    if isinstance(raw, float) and raw != value:
        raise LighterFactsError(f"invalid {field}: {raw!r}")
    if isinstance(raw, str) and raw.strip() != str(value):
        raise LighterFactsError(f"invalid {field}: {raw!r}")
    return value


def _text(payload: Mapping[str, Any], field: str) -> str:
    raw = payload.get(field)
    if not isinstance(raw, str) or not raw.strip():
        raise LighterFactsError(f"invalid {field}: {raw!r}")
    return raw.strip()


@dataclass(frozen=True, slots=True)
class LighterMarketFact:
    market_id: int
    symbol: str
    status: str
    min_base_amount: Decimal
    min_quote_amount: Decimal
    size_decimals: int
    price_decimals: int
    quote_decimals: int
    maker_fee_pct: Decimal
    taker_fee_pct: Decimal
    liquidation_fee_pct: Decimal
    order_quote_limit: Decimal
    multiplier: Decimal
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class LighterPositionFact:
    market_id: int
    symbol: str
    initial_margin_fraction: Decimal
    signed_quantity: Decimal
    avg_entry_price: Decimal
    position_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    liquidation_price: Decimal
    total_funding_paid_out: Decimal | None
    margin_mode: int
    allocated_margin: Decimal


@dataclass(frozen=True, slots=True)
class LighterAccountFact:
    account_index: int
    account_trading_mode: int
    available_balance: Decimal
    collateral: Decimal
    total_asset_value: Decimal
    cross_asset_value: Decimal
    cross_initial_margin_requirement: Decimal
    cross_maintenance_margin_requirement: Decimal
    positions: tuple[LighterPositionFact, ...]
    observed_at: datetime


def parse_lighter_market_fact(
    payload: Mapping[str, Any],
    *,
    observed_at: datetime,
) -> LighterMarketFact:
    """Normalize one perpetual-market metadata row from Lighter."""

    market_type = _text(payload, "market_type").lower()
    if market_type != "perp":
        raise LighterFactsError(
            f"market_type must be perp for this provider boundary, got {market_type!r}"
        )

    status = _text(payload, "status").lower()
    if status not in {"active", "inactive"}:
        raise LighterFactsError(f"invalid status: {status!r}")

    size_decimals = _int(payload, "supported_size_decimals")
    price_decimals = _int(payload, "supported_price_decimals")
    quote_decimals = _int(payload, "supported_quote_decimals")
    if min(size_decimals, price_decimals, quote_decimals) < 0:
        raise LighterFactsError("supported decimal precision cannot be negative")

    return LighterMarketFact(
        market_id=_int(payload, "market_id"),
        symbol=_text(payload, "symbol"),
        status=status,
        min_base_amount=_decimal(payload, "min_base_amount"),
        min_quote_amount=_decimal(payload, "min_quote_amount"),
        size_decimals=size_decimals,
        price_decimals=price_decimals,
        quote_decimals=quote_decimals,
        maker_fee_pct=_decimal(payload, "maker_fee"),
        taker_fee_pct=_decimal(payload, "taker_fee"),
        liquidation_fee_pct=_decimal(payload, "liquidation_fee"),
        order_quote_limit=_decimal(payload, "order_quote_limit"),
        multiplier=_decimal(payload, "multiplier"),
        observed_at=_observed_at(observed_at),
    )


def _parse_position(payload: Mapping[str, Any]) -> LighterPositionFact:
    sign = _int(payload, "sign")
    if sign not in {-1, 0, 1}:
        raise LighterFactsError(f"invalid sign: {sign!r}")

    quantity = _decimal(payload, "position")
    if quantity < 0:
        raise LighterFactsError("position magnitude cannot be negative")

    return LighterPositionFact(
        market_id=_int(payload, "market_id"),
        symbol=_text(payload, "symbol"),
        initial_margin_fraction=_decimal(payload, "initial_margin_fraction"),
        signed_quantity=quantity * Decimal(sign),
        avg_entry_price=_decimal(payload, "avg_entry_price"),
        position_value=_decimal(payload, "position_value"),
        unrealized_pnl=_decimal(payload, "unrealized_pnl"),
        realized_pnl=_decimal(payload, "realized_pnl"),
        liquidation_price=_decimal(payload, "liquidation_price"),
        total_funding_paid_out=_optional_decimal(payload, "total_funding_paid_out"),
        margin_mode=_int(payload, "margin_mode"),
        allocated_margin=_decimal(payload, "allocated_margin"),
    )


def parse_lighter_account_fact(
    payload: Mapping[str, Any],
    *,
    observed_at: datetime,
) -> LighterAccountFact:
    """Normalize one detailed-account payload including margin configuration."""

    moment = _observed_at(observed_at)
    raw_positions = payload.get("positions")
    if not isinstance(raw_positions, list):
        raise LighterFactsError("positions must be a list")

    positions: list[LighterPositionFact] = []
    for index, raw_position in enumerate(raw_positions):
        if not isinstance(raw_position, Mapping):
            raise LighterFactsError(f"invalid positions[{index}]")
        positions.append(_parse_position(raw_position))

    return LighterAccountFact(
        account_index=_int(payload, "account_index"),
        account_trading_mode=_int(payload, "account_trading_mode"),
        available_balance=_decimal(payload, "available_balance"),
        collateral=_decimal(payload, "collateral"),
        total_asset_value=_decimal(payload, "total_asset_value"),
        cross_asset_value=_decimal(payload, "cross_asset_value"),
        cross_initial_margin_requirement=_decimal(
            payload, "cross_initial_margin_requirement"
        ),
        cross_maintenance_margin_requirement=_decimal(
            payload, "cross_maintenance_margin_requirement"
        ),
        positions=tuple(positions),
        observed_at=moment,
    )


__all__ = [
    "LighterAccountFact",
    "LighterFactsError",
    "LighterMarketFact",
    "LighterPositionFact",
    "parse_lighter_account_fact",
    "parse_lighter_market_fact",
]
