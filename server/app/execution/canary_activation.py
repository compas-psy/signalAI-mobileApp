"""Non-authorizing owner readiness for future SANDBOX→CANARY activation.

The owner has approved the static Canary v1 risk envelope and five-minute step-up
TTL. This module still stops before challenge issuance and real-money activation:
profile approval is deliberately separate from final LIVE authorization.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.canary_policy import CanaryPolicySnapshot
from ..models.execution import ExecutionModeState
from .canary_policy import verify_persisted_canary_snapshot
from .canary_preflight import (
    CanaryPreflightError,
    CanaryRuntimeContext,
    evaluate_canary_preflight,
)
from .canary_profile_v1 import (
    CANARY_V1_CHALLENGE_TTL_SECONDS,
    validate_canary_v1_payload,
)
from .enums import ExecutionLifecycleMode

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_LEGACY_APPROVAL_PLACEHOLDERS = frozenset(
    {"ADR_0002_NOT_ACCEPTED", "CANARY_OWNER_STEP_UP_NOT_IMPLEMENTED"}
)
_FINAL_OWNER_BLOCKER = "FINAL_OWNER_ACTIVATION_REQUIRED"


class CanaryActivationReadinessError(ValueError):
    """The exact immutable policy cannot be used for an owner readiness view."""


@dataclass(frozen=True, slots=True)
class CanaryActivationReadiness:
    snapshot_hash: str
    from_mode: ExecutionLifecycleMode
    target_mode: ExecutionLifecycleMode
    venue: str
    strategy_family: str
    strategy_version: str
    account_index: int
    api_key_index: int
    market_allowlist: tuple[int, ...]
    instrument_allowlist: tuple[str, ...]
    capital_amount: Decimal
    capital_currency: str
    valuation_source: str
    valuation_observed_at: str
    valuation_rule: str
    hard_caps: dict[str, object]
    valid_until: str
    structural_checks_passed: bool
    challenge_ttl_seconds: int
    challenge_issuable: bool
    blockers: tuple[str, ...]


RuntimeContextProvider = Callable[[], CanaryRuntimeContext]


def _exact_snapshot(db: Session, snapshot_hash: str) -> CanaryPolicySnapshot:
    normalized = str(snapshot_hash).strip().lower()
    if _HEX64.fullmatch(normalized) is None:
        raise CanaryActivationReadinessError(
            "snapshot_hash must be a SHA-256 hex digest"
        )
    row = db.execute(
        select(CanaryPolicySnapshot).where(
            CanaryPolicySnapshot.snapshot_hash == normalized
        )
    ).scalar_one_or_none()
    if row is None:
        raise CanaryActivationReadinessError("Canary policy snapshot does not exist")
    return row


def _current_mode_read_only(db: Session) -> ExecutionLifecycleMode:
    row = db.execute(
        select(ExecutionModeState).where(ExecutionModeState.id == 1)
    ).scalar_one_or_none()
    if row is None:
        return ExecutionLifecycleMode.PAPER
    return ExecutionLifecycleMode(row.mode)


def _tuple_ints(value: object) -> tuple[int, ...]:
    if not isinstance(value, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in value
    ):
        raise CanaryActivationReadinessError("Canary market allowlist is invalid")
    return tuple(value)


def _tuple_text(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CanaryActivationReadinessError("Canary instrument allowlist is invalid")
    return tuple(value)


def build_canary_mode_event_detail(snapshot: CanaryPolicySnapshot) -> dict[str, object]:
    payload = verify_persisted_canary_snapshot(snapshot)
    return {
        "canary_policy_snapshot_hash": snapshot.snapshot_hash,
        "correlation_id": snapshot.correlation_id,
        "source_sha": snapshot.source_sha,
        "engine_config_hash": snapshot.engine_config_hash,
        "policy_version": payload["policy_version"],
        "credential_generation_id": payload["credential_generation_id"],
        "account_index": payload["account_index"],
        "api_key_index": payload["api_key_index"],
        "strategy_family": payload["strategy_family"],
        "strategy_version": payload["strategy_version"],
        "market_allowlist": list(payload["market_allowlist"]),
        "instrument_allowlist": list(payload["instrument_allowlist"]),
        "capital_amount": payload["capital_amount"],
        "capital_currency": payload["capital_currency"],
        "valuation_source": payload["valuation_source"],
        "valuation_observed_at": payload["valuation_observed_at"],
        "valuation_rule": payload["valuation_rule"],
        "hard_caps": dict(payload["hard_caps"]),
        "valid_until": payload["valid_until"],
        "challenge_ttl_seconds": CANARY_V1_CHALLENGE_TTL_SECONDS,
    }


def build_canary_activation_readiness(
    db: Session,
    *,
    snapshot_hash: str,
    context_provider: RuntimeContextProvider,
) -> CanaryActivationReadiness:
    """Bind one exact immutable Canary v1 policy to a fresh non-authorizing preflight."""
    snapshot = _exact_snapshot(db, snapshot_hash)
    payload = snapshot.payload_json
    if not isinstance(payload, dict):
        raise CanaryActivationReadinessError("Canary policy payload is invalid")

    try:
        preflight = evaluate_canary_preflight(
            db,
            snapshot_hash=snapshot.snapshot_hash,
            context_provider=context_provider,
        )
    except CanaryPreflightError as exc:
        raise CanaryActivationReadinessError(str(exc)) from exc

    blockers = [
        blocker
        for blocker in preflight.blockers
        if blocker not in _LEGACY_APPROVAL_PLACEHOLDERS
    ]
    profile_blockers = validate_canary_v1_payload(payload)
    for blocker in profile_blockers:
        if blocker not in blockers:
            blockers.append(blocker)
    if preflight.structural_checks_passed and not profile_blockers:
        blockers.append(_FINAL_OWNER_BLOCKER)

    try:
        capital_amount = Decimal(str(payload["capital_amount"]))
        hard_caps = payload["hard_caps"]
        if not isinstance(hard_caps, dict):
            raise TypeError("hard caps must be an object")
        result = CanaryActivationReadiness(
            snapshot_hash=snapshot.snapshot_hash,
            from_mode=_current_mode_read_only(db),
            target_mode=ExecutionLifecycleMode.CANARY,
            venue=str(payload["venue"]),
            strategy_family=str(payload["strategy_family"]),
            strategy_version=str(payload["strategy_version"]),
            account_index=int(payload["account_index"]),
            api_key_index=int(payload["api_key_index"]),
            market_allowlist=_tuple_ints(payload["market_allowlist"]),
            instrument_allowlist=_tuple_text(payload["instrument_allowlist"]),
            capital_amount=capital_amount,
            capital_currency=str(payload["capital_currency"]),
            valuation_source=str(payload["valuation_source"]),
            valuation_observed_at=str(payload["valuation_observed_at"]),
            valuation_rule=str(payload["valuation_rule"]),
            hard_caps=dict(hard_caps),
            valid_until=str(payload["valid_until"]),
            structural_checks_passed=preflight.structural_checks_passed,
            challenge_ttl_seconds=CANARY_V1_CHALLENGE_TTL_SECONDS,
            challenge_issuable=False,
            blockers=tuple(blockers),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CanaryActivationReadinessError(
            "Canary policy payload cannot be rendered safely"
        ) from exc

    return result


__all__ = [
    "CanaryActivationReadiness",
    "CanaryActivationReadinessError",
    "build_canary_activation_readiness",
    "build_canary_mode_event_detail",
]
