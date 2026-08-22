"""Pure fail-closed evaluation of a proposed future Lighter Canary entry.

This module is deliberately non-authorizing.  It has no database, credential,
provider, network, execution-mode or order-submission dependency.  A caller may
use a positive decision only as one input to a later submit-time guard; it is
never sufficient authority on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


_REQUIRED_CAPS = frozenset(
    {
        "max_order_notional",
        "max_instrument_notional",
        "max_gross_notional",
        "max_open_positions",
        "max_entry_orders",
        "max_leverage",
        "daily_loss_limit",
        "total_loss_limit",
        "max_order_count",
        "max_trade_count",
    }
)
_COUNT_CAPS = frozenset(
    {"max_open_positions", "max_entry_orders", "max_order_count", "max_trade_count"}
)


@dataclass(frozen=True, slots=True)
class CanaryEntryProposal:
    instrument_id: str
    market_index: int
    order_notional: Decimal
    leverage: Decimal
    max_loss_amount: Decimal
    creates_position: bool


@dataclass(frozen=True, slots=True)
class CanaryExposureState:
    gross_notional: Decimal
    instrument_notional: Decimal
    open_positions: int
    entry_orders: int
    order_count: int
    trade_count: int
    daily_loss: Decimal
    total_loss: Decimal


@dataclass(frozen=True, slots=True)
class CanaryDynamicLimits:
    """Fresh authoritative limits collected outside this pure evaluator."""

    risk_engine_order_notional: Decimal | None
    account_order_notional: Decimal | None
    provider_order_notional: Decimal | None


@dataclass(frozen=True, slots=True)
class CanaryLimitDecision:
    allowed: bool
    blockers: tuple[str, ...]
    effective_order_notional_cap: Decimal | None


def _decimal(value: Any, *, positive: bool) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("invalid decimal") from exc
    if not parsed.is_finite():
        raise ValueError("decimal must be finite")
    if positive and parsed <= 0:
        raise ValueError("decimal must be positive")
    if not positive and parsed < 0:
        raise ValueError("decimal must be non-negative")
    return parsed


def _count(value: Any, *, positive: bool) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("count must be an integer")
    if positive and value <= 0:
        raise ValueError("count must be positive")
    if not positive and value < 0:
        raise ValueError("count must be non-negative")
    return value


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp is missing")
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _policy_values(policy: Mapping[str, Any]) -> tuple[
    frozenset[int],
    frozenset[str],
    Decimal,
    dict[str, Decimal | int],
    datetime,
]:
    if (
        policy.get("schema_version") != 1
        or policy.get("venue") != "LIGHTER"
        or policy.get("environment") != "mainnet"
    ):
        raise ValueError("policy identity is invalid")

    markets_raw = policy.get("market_allowlist")
    instruments_raw = policy.get("instrument_allowlist")
    if not isinstance(markets_raw, list) or not markets_raw:
        raise ValueError("market allowlist is invalid")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in markets_raw):
        raise ValueError("market allowlist is invalid")
    if len(set(markets_raw)) != len(markets_raw):
        raise ValueError("market allowlist contains duplicates")
    if not isinstance(instruments_raw, list) or not instruments_raw:
        raise ValueError("instrument allowlist is invalid")
    if any(not isinstance(value, str) or not value.strip() for value in instruments_raw):
        raise ValueError("instrument allowlist is invalid")
    if len(set(instruments_raw)) != len(instruments_raw):
        raise ValueError("instrument allowlist contains duplicates")

    caps_raw = policy.get("hard_caps")
    if not isinstance(caps_raw, Mapping) or set(caps_raw) != _REQUIRED_CAPS:
        raise ValueError("hard caps are incomplete")
    caps: dict[str, Decimal | int] = {}
    for key in _REQUIRED_CAPS:
        if key in _COUNT_CAPS:
            caps[key] = _count(caps_raw[key], positive=True)
        else:
            caps[key] = _decimal(caps_raw[key], positive=True)

    capital = _decimal(policy.get("capital_amount"), positive=True)
    valid_until = _timestamp(policy.get("valid_until"))
    return (
        frozenset(markets_raw),
        frozenset(value.strip() for value in instruments_raw),
        capital,
        caps,
        valid_until,
    )


def _validate_proposal(proposal: CanaryEntryProposal) -> None:
    if (
        not isinstance(proposal.instrument_id, str)
        or not proposal.instrument_id.strip()
        or isinstance(proposal.market_index, bool)
        or not isinstance(proposal.market_index, int)
        or proposal.market_index < 0
        or not isinstance(proposal.creates_position, bool)
    ):
        raise ValueError("proposal identity is invalid")
    _decimal(proposal.order_notional, positive=True)
    _decimal(proposal.leverage, positive=True)
    _decimal(proposal.max_loss_amount, positive=True)


def _validate_exposure(exposure: CanaryExposureState) -> None:
    _decimal(exposure.gross_notional, positive=False)
    _decimal(exposure.instrument_notional, positive=False)
    _decimal(exposure.daily_loss, positive=False)
    _decimal(exposure.total_loss, positive=False)
    _count(exposure.open_positions, positive=False)
    _count(exposure.entry_orders, positive=False)
    _count(exposure.order_count, positive=False)
    _count(exposure.trade_count, positive=False)


def _dynamic_order_cap(limits: CanaryDynamicLimits, policy_cap: Decimal) -> Decimal:
    values = (
        limits.risk_engine_order_notional,
        limits.account_order_notional,
        limits.provider_order_notional,
    )
    parsed = [_decimal(value, positive=True) for value in values]
    return min(policy_cap, *parsed)


def evaluate_canary_entry_limits(
    policy: Mapping[str, Any],
    *,
    proposal: CanaryEntryProposal,
    exposure: CanaryExposureState,
    dynamic_limits: CanaryDynamicLimits,
    now: datetime,
) -> CanaryLimitDecision:
    """Evaluate prospective entry limits without granting execution authority."""

    try:
        markets, instruments, capital, caps, valid_until = _policy_values(policy)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        moment = now.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return CanaryLimitDecision(False, ("POLICY_INVALID",), None)

    if moment >= valid_until:
        return CanaryLimitDecision(False, ("POLICY_EXPIRED",), None)

    try:
        _validate_proposal(proposal)
    except (ValueError, TypeError, AttributeError):
        return CanaryLimitDecision(False, ("PROPOSAL_INVALID",), None)

    try:
        _validate_exposure(exposure)
    except (ValueError, TypeError, AttributeError):
        return CanaryLimitDecision(False, ("EXPOSURE_INVALID",), None)

    try:
        effective_order_cap = _dynamic_order_cap(
            dynamic_limits,
            caps["max_order_notional"],  # type: ignore[arg-type]
        )
    except (ValueError, TypeError, AttributeError):
        return CanaryLimitDecision(
            False,
            ("DYNAMIC_LIMIT_MISSING_OR_INVALID",),
            None,
        )

    blockers: list[str] = []
    instrument_id = proposal.instrument_id.strip()
    order_notional = _decimal(proposal.order_notional, positive=True)
    leverage = _decimal(proposal.leverage, positive=True)
    max_loss = _decimal(proposal.max_loss_amount, positive=True)
    gross = _decimal(exposure.gross_notional, positive=False)
    instrument = _decimal(exposure.instrument_notional, positive=False)
    daily_loss = _decimal(exposure.daily_loss, positive=False)
    total_loss = _decimal(exposure.total_loss, positive=False)

    if proposal.market_index not in markets:
        blockers.append("MARKET_NOT_ALLOWED")
    if instrument_id not in instruments:
        blockers.append("INSTRUMENT_NOT_ALLOWED")
    if order_notional > effective_order_cap:
        blockers.append("ORDER_NOTIONAL_LIMIT")
    if instrument + order_notional > caps["max_instrument_notional"]:
        blockers.append("INSTRUMENT_NOTIONAL_LIMIT")
    prospective_gross = gross + order_notional
    if prospective_gross > caps["max_gross_notional"]:
        blockers.append("GROSS_NOTIONAL_LIMIT")
    if prospective_gross > capital * caps["max_leverage"]:
        blockers.append("CAPITAL_LEVERAGE_LIMIT")
    if leverage > caps["max_leverage"]:
        blockers.append("LEVERAGE_LIMIT")

    prospective_positions = exposure.open_positions + (1 if proposal.creates_position else 0)
    if prospective_positions > caps["max_open_positions"]:
        blockers.append("OPEN_POSITIONS_LIMIT")
    if exposure.entry_orders + 1 > caps["max_entry_orders"]:
        blockers.append("ENTRY_ORDERS_LIMIT")
    if exposure.order_count + 1 > caps["max_order_count"]:
        blockers.append("ORDER_COUNT_LIMIT")
    if exposure.trade_count + 1 > caps["max_trade_count"]:
        blockers.append("TRADE_COUNT_LIMIT")
    if daily_loss + max_loss > caps["daily_loss_limit"]:
        blockers.append("DAILY_LOSS_LIMIT")
    if total_loss + max_loss > caps["total_loss_limit"]:
        blockers.append("TOTAL_LOSS_LIMIT")

    return CanaryLimitDecision(
        allowed=not blockers,
        blockers=tuple(blockers),
        effective_order_notional_cap=effective_order_cap,
    )


__all__ = [
    "CanaryDynamicLimits",
    "CanaryEntryProposal",
    "CanaryExposureState",
    "CanaryLimitDecision",
    "evaluate_canary_entry_limits",
]
