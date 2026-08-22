"""Serialized, non-authorizing submit-time checks for a future Lighter Canary.

The context manager deliberately holds the same execution-control advisory lock
used by the kill switch for the entire evaluation and for the caller's ``with``
body.  A future provider adapter may therefore perform its final network write
inside this boundary only after separate activation/owner-authority work exists.

This module itself cannot authorize provider I/O.  Even a structurally clean
result remains blocked by unresolved ADR-0002 and cryptographic owner step-up.
It imports no Lighter SDK, secret decryption, signer, transport or order sink.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Callable, Iterator, Mapping

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models.canary_policy import CanaryPolicySnapshot
from ..models.execution import ExecutionModeState
from ..models.risk import RiskState
from .canary_limits import (
    CanaryDynamicLimits,
    CanaryEntryProposal,
    CanaryExposureState,
    evaluate_canary_entry_limits,
)
from .canary_policy import current_lighter_trade_generation
from .canary_preflight import CanaryRuntimeContext
from .enums import ExecutionKillSwitchLevel, ExecutionLifecycleMode
from .kill_switch import execution_control_lock, effective_execution_kill_switch_level

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_GOVERNANCE_BLOCKERS = (
    "ADR_0002_NOT_ACCEPTED",
    "CANARY_OWNER_STEP_UP_NOT_IMPLEMENTED",
)


@dataclass(frozen=True, slots=True)
class CanarySubmitGuardResult:
    """Non-secret result that is never sufficient execution authority."""

    provider_io_eligible: bool
    structural_checks_passed: bool
    blockers: tuple[str, ...]
    effective_order_notional_cap: Decimal | None = None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "provider_io_eligible": self.provider_io_eligible,
            "structural_checks_passed": self.structural_checks_passed,
            "blockers": list(self.blockers),
            "effective_order_notional_cap": (
                str(self.effective_order_notional_cap)
                if self.effective_order_notional_cap is not None
                else None
            ),
        }


RuntimeContextProvider = Callable[[], CanaryRuntimeContext]
ExposureProvider = Callable[[], CanaryExposureState]
DynamicLimitsProvider = Callable[[], CanaryDynamicLimits]


def _result(
    blockers: list[str] | tuple[str, ...],
    *,
    structural_checks_passed: bool,
    effective_order_notional_cap: Decimal | None = None,
) -> CanarySubmitGuardResult:
    return CanarySubmitGuardResult(
        provider_io_eligible=False,
        structural_checks_passed=structural_checks_passed,
        blockers=tuple(blockers),
        effective_order_notional_cap=effective_order_notional_cap,
    )


def _canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _row_binding_is_consistent(
    snapshot: CanaryPolicySnapshot,
    payload: Mapping[str, Any],
) -> bool:
    return (
        payload.get("source_sha") == snapshot.source_sha
        and payload.get("engine_config_hash") == snapshot.engine_config_hash
        and payload.get("credential_generation_id")
        == str(snapshot.credential_generation_id)
        and payload.get("account_index") == snapshot.account_index
        and payload.get("api_key_index") == snapshot.api_key_index
        and payload.get("strategy_family") == snapshot.strategy_family
        and payload.get("strategy_version") == snapshot.strategy_version
    )


def _fresh_mode(db: Session) -> ExecutionLifecycleMode:
    state = db.execute(
        select(ExecutionModeState)
        .where(ExecutionModeState.id == 1)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if state is None:
        return ExecutionLifecycleMode.PAPER
    return ExecutionLifecycleMode(state.mode)


def _fresh_kill_switch(db: Session) -> ExecutionKillSwitchLevel:
    state = db.execute(
        select(RiskState)
        .where(RiskState.id == 1)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    return effective_execution_kill_switch_level(state)


def _live_secret_configured(db: Session) -> bool:
    row = db.execute(
        text(
            "SELECT 1 FROM signalai_integration_secrets "
            "WHERE slot = 'lighter_trade' LIMIT 1"
        )
    ).first()
    return row is not None


def _load_exact_policy(
    db: Session,
    snapshot_hash: str,
) -> tuple[CanaryPolicySnapshot | None, Mapping[str, Any] | None, tuple[str, ...]]:
    normalized = str(snapshot_hash).strip().lower()
    if _HEX64.fullmatch(normalized) is None:
        return None, None, ("CANARY_POLICY_HASH_INVALID",)

    snapshot = db.execute(
        select(CanaryPolicySnapshot).where(
            CanaryPolicySnapshot.snapshot_hash == normalized
        )
    ).scalar_one_or_none()
    if snapshot is None:
        return None, None, ("CANARY_POLICY_NOT_FOUND",)

    payload = snapshot.payload_json
    if not isinstance(payload, Mapping):
        return snapshot, None, ("CANARY_POLICY_HASH_INTEGRITY_FAILED",)
    if _canonical_payload_hash(payload) != normalized:
        return snapshot, payload, ("CANARY_POLICY_HASH_INTEGRITY_FAILED",)
    if not _row_binding_is_consistent(snapshot, payload):
        return snapshot, payload, ("CANARY_POLICY_ROW_BINDING_MISMATCH",)
    return snapshot, payload, ()


def _evaluate_locked(
    db: Session,
    *,
    snapshot_hash: str,
    context_provider: RuntimeContextProvider,
    proposal: CanaryEntryProposal,
    exposure_provider: ExposureProvider,
    dynamic_limits_provider: DynamicLimitsProvider,
) -> CanarySubmitGuardResult:
    """Evaluate all mutable facts while ``execution_control_lock`` is held."""

    snapshot, payload, policy_blockers = _load_exact_policy(db, snapshot_hash)
    if policy_blockers:
        return _result(policy_blockers, structural_checks_passed=False)
    assert snapshot is not None and payload is not None

    blockers: list[str] = []
    if _fresh_mode(db) is not ExecutionLifecycleMode.CANARY:
        blockers.append("EXECUTION_MODE_NOT_CANARY")
    if _fresh_kill_switch(db) is not ExecutionKillSwitchLevel.CLEAR:
        blockers.append("EXECUTION_KILL_SWITCH_NOT_CLEAR")
    if blockers:
        return _result(blockers, structural_checks_passed=False)

    try:
        context = context_provider()
    except Exception:
        return _result(("CANARY_RUNTIME_CONTEXT_UNAVAILABLE",), structural_checks_passed=False)
    if not isinstance(context, CanaryRuntimeContext):
        return _result(("CANARY_RUNTIME_CONTEXT_INVALID",), structural_checks_passed=False)

    source_sha = str(context.source_sha).strip().lower()
    if _HEX40.fullmatch(source_sha) is None:
        blockers.append("DEPLOYED_SOURCE_SHA_UNKNOWN")
    elif source_sha != snapshot.source_sha:
        blockers.append("DEPLOYED_SOURCE_SHA_MISMATCH")

    config_hash = str(context.config_hash).strip().lower()
    if _HEX64.fullmatch(config_hash) is None or config_hash != snapshot.engine_config_hash:
        blockers.append("ENGINE_CONFIG_HASH_MISMATCH")
    if context.paper_only:
        blockers.append("RISK_PAPER_ONLY")

    now = datetime.now(UTC)
    valid_until = snapshot.valid_until
    if valid_until.tzinfo is None or valid_until.utcoffset() is None:
        blockers.append("CANARY_POLICY_EXPIRY_INVALID")
    elif valid_until.astimezone(UTC) <= now:
        blockers.append("CANARY_POLICY_EXPIRED")

    live_configured = _live_secret_configured(db)
    generation = current_lighter_trade_generation(db)
    if not live_configured:
        blockers.append("LIGHTER_LIVE_CREDENTIAL_NOT_CONFIGURED")
    if generation is None:
        blockers.append("LIVE_CREDENTIAL_GENERATION_MISSING_OR_REVOKED")
    else:
        if generation.generation_id != str(snapshot.credential_generation_id):
            blockers.append("CREDENTIAL_GENERATION_MISMATCH")
        if (
            generation.account_index != snapshot.account_index
            or generation.api_key_index != snapshot.api_key_index
        ):
            blockers.append("CREDENTIAL_SCOPE_MISMATCH")

    if blockers:
        return _result(blockers, structural_checks_passed=False)

    try:
        exposure = exposure_provider()
    except Exception:
        return _result(("CANARY_EXPOSURE_UNAVAILABLE",), structural_checks_passed=False)
    try:
        dynamic_limits = dynamic_limits_provider()
    except Exception:
        return _result(("CANARY_DYNAMIC_LIMITS_UNAVAILABLE",), structural_checks_passed=False)

    decision = evaluate_canary_entry_limits(
        payload,
        proposal=proposal,
        exposure=exposure,
        dynamic_limits=dynamic_limits,
        now=now,
    )
    if not decision.allowed:
        return _result(
            decision.blockers,
            structural_checks_passed=False,
            effective_order_notional_cap=decision.effective_order_notional_cap,
        )

    # This is intentionally the terminal state for the current repository.
    # Passing structural checks does not grant authority or imply that Canary
    # activation is accepted. A future owner-step-up slice must replace these
    # blockers explicitly and keep provider I/O inside this same lock scope.
    return _result(
        _GOVERNANCE_BLOCKERS,
        structural_checks_passed=True,
        effective_order_notional_cap=decision.effective_order_notional_cap,
    )


@contextmanager
def canary_submit_guard(
    db: Session,
    *,
    snapshot_hash: str,
    context_provider: RuntimeContextProvider,
    proposal: CanaryEntryProposal,
    exposure_provider: ExposureProvider,
    dynamic_limits_provider: DynamicLimitsProvider,
) -> Iterator[CanarySubmitGuardResult]:
    """Hold execution serialization across final mutable-fact evaluation.

    The yielded result is always ``provider_io_eligible=False`` in this slice.
    Keeping the lock through the caller's ``with`` body prevents a later safe
    transport integration from accidentally creating a check-then-submit race.
    """

    with execution_control_lock(db):
        yield _evaluate_locked(
            db,
            snapshot_hash=snapshot_hash,
            context_provider=context_provider,
            proposal=proposal,
            exposure_provider=exposure_provider,
            dynamic_limits_provider=dynamic_limits_provider,
        )


__all__ = [
    "CanarySubmitGuardResult",
    "canary_submit_guard",
]
