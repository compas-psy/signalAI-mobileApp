"""Deterministic leverage/liquidation proof for SAI-045.

The manual-risk path must never accept leverage or liquidation economics from a
client.  This module consumes only server-owned, short-lived margin facts and
returns the *minimum* leverage required to fund the planned linear perpetual
position, rounded up to the venue step.  It then derives the isolated-margin
liquidation price and enforces the existing SignalAI liquidation-distance cap.

The formulas implemented here are the linear USDT-perpetual isolated-margin
formulas used by Bybit UTA when no extra margin is manually added.  Provider
network I/O deliberately does not live here: later venue adapters may refresh
and persist the facts, while this pure domain layer remains deterministic and
replayable for preview verification/audit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Mapping, Sequence

from ..models.enums import Direction


class LeverageLiquidationRejected(ValueError):
    """A server-owned margin proof cannot safely authorize the planned size."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = str(code)
        self.detail = str(detail)
        super().__init__(f"{self.code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class LeverageTier:
    """One provider risk-limit tier for a linear perpetual instrument."""

    tier_id: int
    risk_limit_value: Decimal
    initial_margin_rate: Decimal
    maintenance_margin_rate: Decimal
    max_leverage: Decimal
    maintenance_margin_deduction: Decimal


@dataclass(frozen=True, slots=True)
class LinearIsolatedMarginFacts:
    """Short-lived server fact-set used to prove one manual-risk preview.

    ``available_margin`` is the already-reserved/approved quote-currency margin
    budget that this position may consume; it is not accepted from the mobile
    client. ``exposure_before`` is provider risk-tier exposure already counting
    against this symbol before the planned order (for example existing provider
    order value). SignalAI's one-live-trade-per-instrument invariant prevents
    this field from silently representing a second independently managed
    position.
    """

    source: str
    source_ref: str
    observed_at: datetime
    expires_at: datetime
    venue: str
    account: str
    symbol: str
    margin_mode: str
    available_margin: Decimal
    exposure_before: Decimal
    leverage_step: Decimal
    tiers: tuple[LeverageTier, ...]


@dataclass(frozen=True, slots=True)
class LeverageLiquidationProof:
    position_notional: Decimal
    total_exposure: Decimal
    required_leverage: Decimal
    leverage: Decimal
    tier_id: int
    initial_margin: Decimal
    maintenance_margin: Decimal
    liquidation_price: Decimal
    liquidation_distance_ratio: Decimal
    margin_proof_hash: str


def _normalise_instant(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _decimal_text(value: Decimal) -> str:
    text = format(Decimal(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _parse_decimal(raw: object, *, label: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_FACTS",
            f"{label} must be decimal",
        ) from exc
    if not value.is_finite():
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_FACTS",
            f"{label} must be finite",
        )
    return value


def _parse_datetime(raw: object, *, label: str) -> datetime:
    if isinstance(raw, datetime):
        return _normalise_instant(raw)
    if not isinstance(raw, str) or not raw.strip():
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_FACTS",
            f"{label} must be an ISO-8601 timestamp",
        )
    value = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_FACTS",
            f"{label} must be an ISO-8601 timestamp",
        ) from exc
    return _normalise_instant(parsed)


def _tier_from_mapping(raw: Mapping[str, object]) -> LeverageTier:
    try:
        tier_id = int(raw.get("tier_id"))
    except (TypeError, ValueError) as exc:
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_FACTS",
            "tier_id must be integer",
        ) from exc
    return LeverageTier(
        tier_id=tier_id,
        risk_limit_value=_parse_decimal(
            raw.get("risk_limit_value"), label="risk_limit_value"
        ),
        initial_margin_rate=_parse_decimal(
            raw.get("initial_margin_rate"), label="initial_margin_rate"
        ),
        maintenance_margin_rate=_parse_decimal(
            raw.get("maintenance_margin_rate"), label="maintenance_margin_rate"
        ),
        max_leverage=_parse_decimal(raw.get("max_leverage"), label="max_leverage"),
        maintenance_margin_deduction=_parse_decimal(
            raw.get("maintenance_margin_deduction"),
            label="maintenance_margin_deduction",
        ),
    )


def margin_facts_from_metadata(metadata: Mapping[str, object] | None) -> LinearIsolatedMarginFacts:
    """Parse the canonical server-owned SAI-045 fact-set from instrument metadata.

    Instrument metadata is only the persistence seam in this slice; provider
    refresh/network ownership remains with venue-specific work. The shape is
    intentionally strict so a stale legacy field cannot be mistaken for margin
    authority.
    """

    raw = (metadata or {}).get("linear_isolated_margin_facts")
    if raw is None:
        raise LeverageLiquidationRejected(
            "MARGIN_FACTS_MISSING",
            "linear isolated margin facts are not available",
        )
    if not isinstance(raw, Mapping):
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_FACTS",
            "linear_isolated_margin_facts must be a mapping",
        )
    tiers_raw = raw.get("tiers")
    if not isinstance(tiers_raw, Sequence) or isinstance(tiers_raw, (str, bytes)):
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_FACTS",
            "tiers must be a non-empty sequence",
        )
    tiers: list[LeverageTier] = []
    for item in tiers_raw:
        if not isinstance(item, Mapping):
            raise LeverageLiquidationRejected(
                "INVALID_MARGIN_FACTS",
                "every tier must be a mapping",
            )
        tiers.append(_tier_from_mapping(item))
    if not tiers:
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_FACTS",
            "at least one risk tier is required",
        )

    def required_text(name: str) -> str:
        value = raw.get(name)
        if not isinstance(value, str) or not value.strip():
            raise LeverageLiquidationRejected(
                "INVALID_MARGIN_FACTS",
                f"{name} must be non-empty text",
            )
        return value.strip()

    return LinearIsolatedMarginFacts(
        source=required_text("source"),
        source_ref=required_text("source_ref"),
        observed_at=_parse_datetime(raw.get("observed_at"), label="observed_at"),
        expires_at=_parse_datetime(raw.get("expires_at"), label="expires_at"),
        venue=required_text("venue"),
        account=required_text("account"),
        symbol=required_text("symbol"),
        margin_mode=required_text("margin_mode"),
        available_margin=_parse_decimal(
            raw.get("available_margin"), label="available_margin"
        ),
        exposure_before=_parse_decimal(
            raw.get("exposure_before"), label="exposure_before"
        ),
        leverage_step=_parse_decimal(raw.get("leverage_step"), label="leverage_step"),
        tiers=tuple(tiers),
    )


def _validate_facts(
    facts: LinearIsolatedMarginFacts,
    *,
    venue: str,
    account: str,
    symbol: str,
    now: datetime,
) -> tuple[LeverageTier, ...]:
    instant = _normalise_instant(now)
    observed = _normalise_instant(facts.observed_at)
    expires = _normalise_instant(facts.expires_at)
    if observed > instant:
        raise LeverageLiquidationRejected(
            "MARGIN_FACTS_FROM_FUTURE",
            "margin facts were observed after preview time",
        )
    if expires <= instant:
        raise LeverageLiquidationRejected(
            "MARGIN_FACTS_EXPIRED",
            "margin facts expired before preview",
        )
    if expires <= observed:
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_FACTS",
            "margin facts expiry must be after observation time",
        )
    if facts.margin_mode.strip().upper() != "ISOLATED":
        raise LeverageLiquidationRejected(
            "UNSUPPORTED_MARGIN_MODE",
            "deterministic liquidation proof currently requires ISOLATED margin",
        )
    if (
        facts.venue.strip().upper() != venue.strip().upper()
        or facts.account.strip() != account.strip()
        or facts.symbol.strip().upper() != symbol.strip().upper()
    ):
        raise LeverageLiquidationRejected(
            "MARGIN_SCOPE_MISMATCH",
            "margin facts do not match preview venue/account/symbol",
        )
    if not facts.source.strip() or not facts.source_ref.strip():
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_FACTS",
            "margin facts require source provenance",
        )
    if facts.available_margin <= 0:
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_FACTS",
            "available margin must be positive",
        )
    if facts.exposure_before < 0:
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_FACTS",
            "existing risk-tier exposure cannot be negative",
        )
    if facts.leverage_step <= 0:
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_FACTS",
            "leverage step must be positive",
        )
    if not facts.tiers:
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_FACTS",
            "at least one risk tier is required",
        )

    tiers = tuple(sorted(facts.tiers, key=lambda item: item.risk_limit_value))
    seen_ids: set[int] = set()
    previous_limit = Decimal(0)
    for tier in tiers:
        if tier.tier_id in seen_ids:
            raise LeverageLiquidationRejected(
                "INVALID_MARGIN_FACTS",
                "risk tier ids must be unique",
            )
        seen_ids.add(tier.tier_id)
        if tier.risk_limit_value <= previous_limit:
            raise LeverageLiquidationRejected(
                "INVALID_MARGIN_FACTS",
                "risk limits must be strictly increasing",
            )
        previous_limit = tier.risk_limit_value
        if not (Decimal(0) < tier.initial_margin_rate <= Decimal(1)):
            raise LeverageLiquidationRejected(
                "INVALID_MARGIN_FACTS",
                "initial margin rate must be in (0, 1]",
            )
        if not (Decimal(0) <= tier.maintenance_margin_rate < Decimal(1)):
            raise LeverageLiquidationRejected(
                "INVALID_MARGIN_FACTS",
                "maintenance margin rate must be in [0, 1)",
            )
        if tier.max_leverage < Decimal(1):
            raise LeverageLiquidationRejected(
                "INVALID_MARGIN_FACTS",
                "tier max leverage must be at least 1",
            )
        if tier.maintenance_margin_deduction < 0:
            raise LeverageLiquidationRejected(
                "INVALID_MARGIN_FACTS",
                "maintenance margin deduction cannot be negative",
            )
    return tiers


def _facts_hash(facts: LinearIsolatedMarginFacts) -> str:
    tiers = sorted(facts.tiers, key=lambda item: item.risk_limit_value)
    payload = {
        "source": facts.source,
        "source_ref": facts.source_ref,
        "observed_at": _normalise_instant(facts.observed_at).isoformat(),
        "expires_at": _normalise_instant(facts.expires_at).isoformat(),
        "venue": facts.venue,
        "account": facts.account,
        "symbol": facts.symbol,
        "margin_mode": facts.margin_mode.upper(),
        "available_margin": _decimal_text(facts.available_margin),
        "exposure_before": _decimal_text(facts.exposure_before),
        "leverage_step": _decimal_text(facts.leverage_step),
        "tiers": [
            {
                "tier_id": tier.tier_id,
                "risk_limit_value": _decimal_text(tier.risk_limit_value),
                "initial_margin_rate": _decimal_text(tier.initial_margin_rate),
                "maintenance_margin_rate": _decimal_text(
                    tier.maintenance_margin_rate
                ),
                "max_leverage": _decimal_text(tier.max_leverage),
                "maintenance_margin_deduction": _decimal_text(
                    tier.maintenance_margin_deduction
                ),
            }
            for tier in tiers
        ],
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    units = (value / step).to_integral_value(rounding=ROUND_CEILING)
    return units * step


def derive_leverage_liquidation(
    *,
    facts: LinearIsolatedMarginFacts,
    venue: str,
    account: str,
    symbol: str,
    direction: Direction,
    entry: Decimal,
    stop: Decimal,
    quantity: Decimal,
    contract_multiplier: Decimal,
    hard_max_leverage: Decimal,
    min_liquidation_distance_ratio: Decimal,
    now: datetime,
) -> LeverageLiquidationProof:
    """Derive one fail-closed linear isolated-margin proof.

    The chosen leverage is the lowest leverage that makes the planned notional
    fit the server-owned available margin budget. Lower leverage means a wider
    liquidation buffer, so if this minimum feasible leverage fails the hard
    liquidation-distance rule there is no safer leverage that also fits the
    supplied margin budget.
    """

    tiers = _validate_facts(
        facts,
        venue=venue,
        account=account,
        symbol=symbol,
        now=now,
    )
    entry = Decimal(entry)
    stop = Decimal(stop)
    quantity = Decimal(quantity)
    contract_multiplier = Decimal(contract_multiplier)
    hard_max_leverage = Decimal(hard_max_leverage)
    min_liquidation_distance_ratio = Decimal(min_liquidation_distance_ratio)
    if entry <= 0 or quantity <= 0 or contract_multiplier <= 0:
        raise LeverageLiquidationRejected(
            "INVALID_POSITION",
            "entry, quantity and contract multiplier must be positive",
        )
    stop_distance = abs(entry - stop)
    if stop_distance <= 0:
        raise LeverageLiquidationRejected(
            "INVALID_POSITION",
            "stop must differ from entry",
        )
    if hard_max_leverage < Decimal(1):
        raise LeverageLiquidationRejected(
            "INVALID_POLICY",
            "hard max leverage must be at least 1",
        )
    if min_liquidation_distance_ratio <= 0:
        raise LeverageLiquidationRejected(
            "INVALID_POLICY",
            "minimum liquidation distance must be positive",
        )

    position_units = quantity * contract_multiplier
    position_notional = position_units * entry
    total_exposure = facts.exposure_before + position_notional
    tier = next(
        (item for item in tiers if total_exposure <= item.risk_limit_value),
        None,
    )
    if tier is None:
        raise LeverageLiquidationRejected(
            "NO_RISK_TIER_FOR_EXPOSURE",
            f"total exposure {total_exposure} exceeds declared risk tiers",
        )

    required_leverage = max(
        Decimal(1),
        position_notional / facts.available_margin,
    )
    leverage = _ceil_to_step(required_leverage, facts.leverage_step)
    tier_im_cap = Decimal(1) / tier.initial_margin_rate
    effective_cap = min(hard_max_leverage, tier.max_leverage, tier_im_cap)
    if leverage > effective_cap:
        raise LeverageLiquidationRejected(
            "REQUIRED_LEVERAGE_EXCEEDS_CAP",
            f"required leverage {leverage} exceeds hard/tier cap {effective_cap}",
        )

    initial_margin = position_notional / leverage
    maintenance_margin = (
        position_notional * tier.maintenance_margin_rate
        - tier.maintenance_margin_deduction
    )
    if maintenance_margin < 0 or maintenance_margin >= initial_margin:
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_ECONOMICS",
            "maintenance margin must be non-negative and below initial margin",
        )

    mmr = tier.maintenance_margin_rate
    mm_deduction = tier.maintenance_margin_deduction
    direction = Direction(direction)
    if direction is Direction.LONG:
        denominator = position_units * (Decimal(1) - mmr)
        numerator = position_notional - initial_margin - mm_deduction
    else:
        denominator = position_units * (Decimal(1) + mmr)
        numerator = position_notional + initial_margin + mm_deduction
    if denominator <= 0 or numerator <= 0:
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_ECONOMICS",
            "liquidation formula produced a non-positive term",
        )
    liquidation_price = numerator / denominator
    if direction is Direction.LONG and liquidation_price >= entry:
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_ECONOMICS",
            "long liquidation price must be below entry",
        )
    if direction is Direction.SHORT and liquidation_price <= entry:
        raise LeverageLiquidationRejected(
            "INVALID_MARGIN_ECONOMICS",
            "short liquidation price must be above entry",
        )

    liquidation_distance = abs(entry - liquidation_price)
    liquidation_distance_ratio = liquidation_distance / stop_distance
    if liquidation_distance_ratio < min_liquidation_distance_ratio:
        raise LeverageLiquidationRejected(
            "LIQUIDATION_BUFFER_TOO_SMALL",
            f"liquidation buffer {liquidation_distance_ratio}x is below "
            f"required {min_liquidation_distance_ratio}x stop distance",
        )

    return LeverageLiquidationProof(
        position_notional=position_notional,
        total_exposure=total_exposure,
        required_leverage=required_leverage,
        leverage=leverage,
        tier_id=tier.tier_id,
        initial_margin=initial_margin,
        maintenance_margin=maintenance_margin,
        liquidation_price=liquidation_price,
        liquidation_distance_ratio=liquidation_distance_ratio,
        margin_proof_hash=_facts_hash(facts),
    )


__all__ = [
    "LeverageLiquidationProof",
    "LeverageLiquidationRejected",
    "LeverageTier",
    "LinearIsolatedMarginFacts",
    "derive_leverage_liquidation",
    "margin_facts_from_metadata",
]
