"""Fail-closed, non-secret foundation for a future Lighter Canary policy.

Nothing in this module can construct a provider transport, submit an order,
allocate capital or change execution mode.  It only records opaque credential
generations and canonicalizes/persists an immutable policy input.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.canary_policy import CanaryPolicySnapshot, LighterCredentialGeneration

LIGHTER_LIVE_SLOT = "lighter_trade"
CANARY_SCHEMA_VERSION = 1
_REQUIRED_EVIDENCE = frozenset(
    {
        "strategy_performance",
        "shadow",
        "testnet",
        "protection_reconciliation",
        "kill_switch_drill",
        "security_scan",
        "operational_health",
    }
)
_REQUIRED_HARD_CAPS = frozenset(
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
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class CanaryPolicyError(ValueError):
    """Raised when a proposed Canary fact cannot be trusted exactly."""


@dataclass(frozen=True, slots=True)
class CredentialGenerationFact:
    generation_id: str
    action: str
    account_index: int
    api_key_index: int
    actor: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CanaryPolicy:
    policy_version: str
    source_sha: str
    engine_config_hash: str
    strategy_family: str
    strategy_version: str
    credential_generation_id: str
    account_index: int
    api_key_index: int
    market_allowlist: tuple[int, ...]
    instrument_allowlist: tuple[str, ...]
    capital_amount: Decimal
    capital_currency: str
    valuation_source: str
    valuation_observed_at: datetime
    valuation_rule: str
    hard_caps: dict[str, Any]
    evidence_refs: dict[str, str]
    valid_until: datetime


@dataclass(frozen=True, slots=True)
class CanonicalCanaryPolicy:
    snapshot_hash: str
    payload: dict[str, Any]


def _bounded_text(value: str, field: str, *, maximum: int = 128) -> str:
    cleaned = str(value).strip()
    if not cleaned or len(cleaned) > maximum or any(c in cleaned for c in "\r\n\x00"):
        raise CanaryPolicyError(f"{field} must be a non-empty bounded single-line value")
    return cleaned


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CanaryPolicyError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _utc_text(value: datetime, field: str) -> str:
    return _utc(value, field).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _positive_decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CanaryPolicyError(f"{field} must be a finite positive decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise CanaryPolicyError(f"{field} must be a finite positive decimal")
    return parsed


def _decimal_text(value: Any, field: str) -> str:
    parsed = _positive_decimal(value, field)
    rendered = format(parsed, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise CanaryPolicyError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CanaryPolicyError(f"{field} must be a positive integer") from exc
    if str(parsed) != str(value).strip() and not isinstance(value, int):
        raise CanaryPolicyError(f"{field} must be a positive integer")
    if parsed <= 0:
        raise CanaryPolicyError(f"{field} must be a positive integer")
    return parsed


def _fact(row: LighterCredentialGeneration) -> CredentialGenerationFact:
    return CredentialGenerationFact(
        generation_id=str(row.generation_id),
        action=row.action,
        account_index=row.account_index,
        api_key_index=row.api_key_index,
        actor=row.actor,
        created_at=row.created_at,
    )


def record_lighter_trade_generation(
    db: Session,
    *,
    action: str,
    actor: str,
    account_index: int,
    api_key_index: int,
) -> CredentialGenerationFact:
    """Append a new opaque generation fact; never derive identity from a secret."""
    normalized_action = str(action).strip().upper()
    if normalized_action not in {"CREATED", "ROTATED", "REVOKED"}:
        raise CanaryPolicyError("credential generation action is invalid")
    normalized_actor = _bounded_text(actor, "actor", maximum=64)
    if account_index < 0 or not 0 <= api_key_index <= 253:
        raise CanaryPolicyError("credential account/API-key scope is invalid")

    row = LighterCredentialGeneration(
        generation_id=uuid.uuid4(),
        slot=LIGHTER_LIVE_SLOT,
        action=normalized_action,
        actor=normalized_actor,
        account_index=account_index,
        api_key_index=api_key_index,
    )
    db.add(row)
    db.flush()
    return _fact(row)


def current_lighter_trade_generation(
    db: Session,
    *,
    include_revoked: bool = False,
) -> CredentialGenerationFact | None:
    row = db.execute(
        select(LighterCredentialGeneration)
        .where(LighterCredentialGeneration.slot == LIGHTER_LIVE_SLOT)
        .order_by(LighterCredentialGeneration.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.action == "REVOKED" and not include_revoked:
        return None
    return _fact(row)


def _canonical_allowlists(policy: CanaryPolicy) -> tuple[list[int], list[str]]:
    markets = list(policy.market_allowlist)
    if not markets or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in markets):
        raise CanaryPolicyError("market_allowlist must contain non-negative integer market ids")
    if len(set(markets)) != len(markets):
        raise CanaryPolicyError("market_allowlist must not contain duplicates")

    instruments = [
        _bounded_text(value, "instrument_allowlist", maximum=128)
        for value in policy.instrument_allowlist
    ]
    if not instruments:
        raise CanaryPolicyError("instrument_allowlist must not be empty")
    if len(set(instruments)) != len(instruments):
        raise CanaryPolicyError("instrument_allowlist must not contain duplicates")
    return sorted(markets), sorted(instruments)


def _canonical_hard_caps(values: Mapping[str, Any]) -> dict[str, Any]:
    if set(values) != _REQUIRED_HARD_CAPS:
        raise CanaryPolicyError(
            "hard_caps must contain exactly the approved Canary hard-cap fields"
        )
    result: dict[str, Any] = {}
    for key in sorted(_REQUIRED_HARD_CAPS):
        if key in _COUNT_CAPS:
            result[key] = _positive_int(values[key], f"hard_caps.{key}")
        else:
            result[key] = _decimal_text(values[key], f"hard_caps.{key}")
    return result


def _canonical_evidence(values: Mapping[str, str]) -> dict[str, str]:
    if set(values) != _REQUIRED_EVIDENCE:
        raise CanaryPolicyError(
            "evidence_refs must contain exactly all required promotion evidence refs"
        )
    return {
        key: _bounded_text(values[key], f"evidence_refs.{key}", maximum=128)
        for key in sorted(_REQUIRED_EVIDENCE)
    }


def canonical_canary_policy(policy: CanaryPolicy) -> CanonicalCanaryPolicy:
    source_sha = str(policy.source_sha).strip().lower()
    config_hash = str(policy.engine_config_hash).strip().lower()
    if _HEX40.fullmatch(source_sha) is None:
        raise CanaryPolicyError("source_sha must be an exact lowercase Git SHA")
    if _HEX64.fullmatch(config_hash) is None:
        raise CanaryPolicyError("engine_config_hash must be an exact SHA-256")

    try:
        generation_id = str(uuid.UUID(str(policy.credential_generation_id)))
    except (ValueError, AttributeError) as exc:
        raise CanaryPolicyError("credential_generation_id must be a UUID") from exc
    if policy.account_index < 0 or not 0 <= policy.api_key_index <= 253:
        raise CanaryPolicyError("credential account/API-key scope is invalid")

    markets, instruments = _canonical_allowlists(policy)
    valuation_at = _utc(policy.valuation_observed_at, "valuation_observed_at")
    valid_until = _utc(policy.valid_until, "valid_until")
    if valid_until <= valuation_at:
        raise CanaryPolicyError("valid_until must be after valuation_observed_at")

    currency = _bounded_text(policy.capital_currency, "capital_currency", maximum=8).upper()
    if not currency.isascii() or not currency.isalpha() or len(currency) < 3:
        raise CanaryPolicyError("capital_currency must be an uppercase currency code")

    payload: dict[str, Any] = {
        "schema_version": CANARY_SCHEMA_VERSION,
        "policy_version": _bounded_text(policy.policy_version, "policy_version", maximum=64),
        "venue": "LIGHTER",
        "environment": "mainnet",
        "source_sha": source_sha,
        "engine_config_hash": config_hash,
        "strategy_family": _bounded_text(policy.strategy_family, "strategy_family", maximum=64),
        "strategy_version": _bounded_text(policy.strategy_version, "strategy_version", maximum=64),
        "credential_generation_id": generation_id,
        "account_index": policy.account_index,
        "api_key_index": policy.api_key_index,
        "market_allowlist": markets,
        "instrument_allowlist": instruments,
        "capital_amount": _decimal_text(policy.capital_amount, "capital_amount"),
        "capital_currency": currency,
        "valuation_source": _bounded_text(policy.valuation_source, "valuation_source", maximum=64),
        "valuation_observed_at": _utc_text(valuation_at, "valuation_observed_at"),
        "valuation_rule": _bounded_text(policy.valuation_rule, "valuation_rule", maximum=128),
        "hard_caps": _canonical_hard_caps(policy.hard_caps),
        "evidence_refs": _canonical_evidence(policy.evidence_refs),
        "valid_until": _utc_text(valid_until, "valid_until"),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return CanonicalCanaryPolicy(
        snapshot_hash=hashlib.sha256(canonical).hexdigest(),
        payload=payload,
    )


def persist_canary_policy_snapshot(
    db: Session,
    policy: CanaryPolicy,
    *,
    actor: str,
    correlation_id: str,
) -> CanaryPolicySnapshot:
    canonical = canonical_canary_policy(policy)
    current = current_lighter_trade_generation(db)
    if current is None:
        raise CanaryPolicyError("current live credential generation is missing or revoked")
    if (
        current.generation_id != canonical.payload["credential_generation_id"]
        or current.account_index != canonical.payload["account_index"]
        or current.api_key_index != canonical.payload["api_key_index"]
    ):
        raise CanaryPolicyError("Canary policy credential generation does not match current scope")

    normalized_actor = _bounded_text(actor, "actor", maximum=64)
    normalized_correlation = _bounded_text(
        correlation_id,
        "correlation_id",
        maximum=128,
    )
    row = CanaryPolicySnapshot(
        snapshot_hash=canonical.snapshot_hash,
        schema_version=CANARY_SCHEMA_VERSION,
        payload_json=canonical.payload,
        source_sha=canonical.payload["source_sha"],
        engine_config_hash=canonical.payload["engine_config_hash"],
        credential_generation_id=uuid.UUID(
            canonical.payload["credential_generation_id"]
        ),
        account_index=canonical.payload["account_index"],
        api_key_index=canonical.payload["api_key_index"],
        strategy_family=canonical.payload["strategy_family"],
        strategy_version=canonical.payload["strategy_version"],
        valid_until=_utc(policy.valid_until, "valid_until"),
        actor=normalized_actor,
        correlation_id=normalized_correlation,
    )
    db.add(row)
    db.flush()
    return row


__all__ = [
    "CANARY_SCHEMA_VERSION",
    "CanaryPolicy",
    "CanaryPolicyError",
    "CanonicalCanaryPolicy",
    "CredentialGenerationFact",
    "canonical_canary_policy",
    "current_lighter_trade_generation",
    "persist_canary_policy_snapshot",
    "record_lighter_trade_generation",
]
