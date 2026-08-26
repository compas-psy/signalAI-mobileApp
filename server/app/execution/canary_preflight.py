"""Read-only fail-closed preflight for a future Lighter Canary run.

This module is deliberately incapable of authorizing a mode change or touching a
provider. It re-checks immutable non-secret policy, runtime, credential and
evidence facts. The owner-approved Canary v1 boundary removes the old unresolved
governance placeholders, but a separate final owner activation is still required.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models.canary_policy import CanaryPolicySnapshot
from ..models.execution import ExecutionModeState
from .canary_evidence import CanaryEvidenceScope, verify_canary_evidence_refs
from .canary_policy import current_lighter_trade_generation
from .canary_profile_v1 import validate_canary_v1_payload
from .enums import ExecutionLifecycleMode

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_FINAL_OWNER_BLOCKER = "FINAL_OWNER_ACTIVATION_REQUIRED"


class CanaryPreflightError(ValueError):
    """Raised only for malformed caller input; readiness itself is a result."""


@dataclass(frozen=True, slots=True)
class CanaryRuntimeContext:
    source_sha: str
    config_hash: str
    paper_only: bool


@dataclass(frozen=True, slots=True)
class CanaryPreflightResult:
    eligible_for_canary: bool
    structural_checks_passed: bool
    blockers: tuple[str, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "eligible_for_canary": self.eligible_for_canary,
            "structural_checks_passed": self.structural_checks_passed,
            "blockers": list(self.blockers),
        }


def _canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _current_mode_read_only(db: Session) -> ExecutionLifecycleMode:
    state = db.execute(
        select(ExecutionModeState).where(ExecutionModeState.id == 1)
    ).scalar_one_or_none()
    if state is None:
        return ExecutionLifecycleMode.PAPER
    return ExecutionLifecycleMode(state.mode)


def _live_secret_configured_read_only(db: Session) -> bool:
    row = db.execute(
        text(
            "SELECT 1 FROM signalai_integration_secrets "
            "WHERE slot = 'lighter_trade' LIMIT 1"
        )
    ).first()
    return row is not None


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


def _failed(*blockers: str) -> CanaryPreflightResult:
    return CanaryPreflightResult(
        eligible_for_canary=False,
        structural_checks_passed=False,
        blockers=tuple(blockers),
    )


def evaluate_canary_preflight(
    db: Session,
    *,
    snapshot_hash: str,
    context_provider: Callable[[], CanaryRuntimeContext],
    payload_override_for_test: Mapping[str, Any] | None = None,
) -> CanaryPreflightResult:
    """Re-check one exact immutable Canary policy without granting authority."""
    normalized_hash = str(snapshot_hash).strip().lower()
    if _HEX64.fullmatch(normalized_hash) is None:
        raise CanaryPreflightError("snapshot_hash must be a SHA-256 hex digest")

    snapshot = db.execute(
        select(CanaryPolicySnapshot).where(
            CanaryPolicySnapshot.snapshot_hash == normalized_hash
        )
    ).scalar_one_or_none()
    if snapshot is None:
        return _failed("CANARY_POLICY_NOT_FOUND")

    payload: Mapping[str, Any] = (
        payload_override_for_test
        if payload_override_for_test is not None
        else snapshot.payload_json
    )
    if not isinstance(payload, Mapping):
        return _failed("CANARY_POLICY_HASH_INTEGRITY_FAILED")
    if _canonical_payload_hash(payload) != normalized_hash:
        return _failed("CANARY_POLICY_HASH_INTEGRITY_FAILED")
    if not _row_binding_is_consistent(snapshot, payload):
        return _failed("CANARY_POLICY_ROW_BINDING_MISMATCH")

    profile_blockers = validate_canary_v1_payload(payload)
    if profile_blockers:
        return _failed(*profile_blockers)

    try:
        context = context_provider()
    except Exception:
        return _failed("CANARY_RUNTIME_CONTEXT_UNAVAILABLE")
    if not isinstance(context, CanaryRuntimeContext):
        return _failed("CANARY_RUNTIME_CONTEXT_INVALID")

    evaluated_at = datetime.now(UTC)
    blockers: list[str] = []

    if _current_mode_read_only(db) is not ExecutionLifecycleMode.SANDBOX:
        blockers.append("EXECUTION_MODE_NOT_SANDBOX")

    source_sha = str(context.source_sha).strip().lower()
    if not source_sha or _HEX40.fullmatch(source_sha) is None:
        blockers.append("DEPLOYED_SOURCE_SHA_UNKNOWN")
    elif source_sha != snapshot.source_sha:
        blockers.append("DEPLOYED_SOURCE_SHA_MISMATCH")

    config_hash = str(context.config_hash).strip().lower()
    if _HEX64.fullmatch(config_hash) is None or config_hash != snapshot.engine_config_hash:
        blockers.append("ENGINE_CONFIG_HASH_MISMATCH")

    if context.paper_only:
        blockers.append("RISK_PAPER_ONLY")

    valid_until = snapshot.valid_until
    if valid_until.tzinfo is None or valid_until.utcoffset() is None:
        blockers.append("CANARY_POLICY_EXPIRY_INVALID")
    elif valid_until.astimezone(UTC) <= evaluated_at:
        blockers.append("CANARY_POLICY_EXPIRED")

    live_configured = _live_secret_configured_read_only(db)
    current_generation = current_lighter_trade_generation(db)
    if not live_configured:
        blockers.append("LIGHTER_LIVE_CREDENTIAL_NOT_CONFIGURED")
    if current_generation is None:
        blockers.append("LIVE_CREDENTIAL_GENERATION_MISSING_OR_REVOKED")
    else:
        if current_generation.generation_id != str(snapshot.credential_generation_id):
            blockers.append("CREDENTIAL_GENERATION_MISMATCH")
        if (
            current_generation.account_index != snapshot.account_index
            or current_generation.api_key_index != snapshot.api_key_index
        ):
            blockers.append("CREDENTIAL_SCOPE_MISMATCH")

    if blockers:
        return _failed(*blockers)

    refs = payload.get("evidence_refs")
    if not isinstance(refs, Mapping):
        return _failed("CANARY_EVIDENCE_REF_SET_INVALID")
    evidence = verify_canary_evidence_refs(
        db,
        evidence_refs={str(key): str(value) for key, value in refs.items()},
        scope=CanaryEvidenceScope(
            source_sha=snapshot.source_sha,
            engine_config_hash=snapshot.engine_config_hash,
            strategy_family=snapshot.strategy_family,
            strategy_version=snapshot.strategy_version,
            venue="LIGHTER",
        ),
        now=evaluated_at,
    )
    if not evidence.complete:
        return _failed(*evidence.blockers)

    # The exact v1 envelope and evidence can be structurally ready, but the
    # owner deliberately separated profile approval from real-money activation.
    return CanaryPreflightResult(
        eligible_for_canary=False,
        structural_checks_passed=True,
        blockers=(_FINAL_OWNER_BLOCKER,),
    )


__all__ = [
    "CanaryPreflightError",
    "CanaryPreflightResult",
    "CanaryRuntimeContext",
    "evaluate_canary_preflight",
]
