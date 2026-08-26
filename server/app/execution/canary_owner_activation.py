"""Cryptographic, replay-safe owner boundary for SANDBOX→CANARY.

This module binds the existing biometric P-256 owner-presence primitive to one
exact immutable Canary v1 policy.  It never provisions credentials, funds an
account or calls a venue.  A mode mutation is possible only after a fresh
signature and a second authoritative readiness/kill-switch recheck under the
shared execution-control lock.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.canary_policy import CanaryPolicySnapshot
from ..models.device import DeviceCredential, OwnerStepUpChallenge
from ..models.risk import AuditEvent, RiskState
from ..owner_step_up import (
    IssuedOwnerStepUpChallenge,
    OwnerStepUpError,
    issue_owner_step_up_challenge,
    verify_owner_step_up_signature,
)
from .canary_activation import (
    CanaryActivationReadinessError,
    RuntimeContextProvider,
    build_canary_activation_readiness,
    build_canary_mode_event_detail,
)
from .canary_profile_v1 import CANARY_V1_CHALLENGE_TTL_SECONDS
from .enums import ExecutionKillSwitchLevel, ExecutionLifecycleMode
from .kill_switch import effective_execution_kill_switch_level, execution_control_lock
from .mode import ModeChangeAuthorization, _change_execution_mode_locked, get_execution_mode

CANARY_OWNER_STEP_UP_PURPOSE = "CANARY_V1_ACTIVATE"
_FINAL_OWNER_BLOCKER = "FINAL_OWNER_ACTIVATION_REQUIRED"
_AUDIT_ACTION = "execution_canary_activation_confirm"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class CanaryOwnerActivationRejected(ValueError):
    """Challenge/confirmation input is missing, unsafe or cannot be trusted."""

    def __init__(self, message: str, *, blockers: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.blockers = blockers


@dataclass(frozen=True, slots=True)
class CanaryOwnerActivationChallenge:
    challenge_id: uuid.UUID
    snapshot_hash: str
    device_id: str
    owner_key_fingerprint: str
    purpose: str
    message: str
    expires_at: object
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class CanaryOwnerActivationResult:
    challenge_id: uuid.UUID
    snapshot_hash: str
    status: str
    mode: ExecutionLifecycleMode
    blockers: tuple[str, ...]


def _snapshot(db: Session, snapshot_hash: str) -> CanaryPolicySnapshot:
    normalized = str(snapshot_hash).strip().lower()
    if _HEX64.fullmatch(normalized) is None:
        raise CanaryOwnerActivationRejected("snapshot_hash must be a SHA-256 hex digest")
    row = db.execute(
        select(CanaryPolicySnapshot).where(CanaryPolicySnapshot.snapshot_hash == normalized)
    ).scalar_one_or_none()
    if row is None:
        raise CanaryOwnerActivationRejected("Canary policy snapshot does not exist")
    return row


def _activation_payload(snapshot: CanaryPolicySnapshot) -> dict[str, object]:
    payload = build_canary_mode_event_detail(snapshot)
    payload.update(
        {
            "owner_action": "ACTIVATE_CANARY_V1",
            "from_mode": ExecutionLifecycleMode.SANDBOX.value,
            "target_mode": ExecutionLifecycleMode.CANARY.value,
        }
    )
    return payload


def _payload_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _kill_switch_blockers(db: Session) -> tuple[str, ...]:
    state = db.get(RiskState, 1)
    if effective_execution_kill_switch_level(state) is ExecutionKillSwitchLevel.CLEAR:
        return ()
    return ("EXECUTION_KILL_SWITCH_NOT_CLEAR",)


def _readiness_blockers(readiness) -> tuple[str, ...]:
    blockers = tuple(str(item) for item in readiness.blockers)
    if (
        readiness.structural_checks_passed
        and readiness.from_mode is ExecutionLifecycleMode.SANDBOX
        and readiness.target_mode is ExecutionLifecycleMode.CANARY
        and blockers == (_FINAL_OWNER_BLOCKER,)
    ):
        return ()
    return blockers or ("CANARY_READINESS_NOT_COMPLETE",)


def _issued(
    challenge: IssuedOwnerStepUpChallenge,
    *,
    snapshot_hash: str,
    payload: dict[str, object],
) -> CanaryOwnerActivationChallenge:
    return CanaryOwnerActivationChallenge(
        challenge_id=challenge.challenge_id,
        snapshot_hash=snapshot_hash,
        device_id=challenge.device_id,
        owner_key_fingerprint=challenge.owner_key_fingerprint,
        purpose=challenge.purpose,
        message=challenge.message,
        expires_at=challenge.expires_at,
        payload=payload,
    )


def issue_canary_owner_activation_challenge(
    db: Session,
    *,
    credential_id: object,
    snapshot_hash: str,
    context_provider: RuntimeContextProvider,
) -> CanaryOwnerActivationChallenge:
    """Issue a five-minute owner challenge only for a fully ready Canary v1."""

    snapshot = _snapshot(db, snapshot_hash)
    try:
        readiness = build_canary_activation_readiness(
            db,
            snapshot_hash=snapshot.snapshot_hash,
            context_provider=context_provider,
        )
    except CanaryActivationReadinessError as exc:
        raise CanaryOwnerActivationRejected(str(exc)) from exc

    blockers = _readiness_blockers(readiness) + _kill_switch_blockers(db)
    if blockers:
        raise CanaryOwnerActivationRejected(
            "Canary activation is not ready for owner step-up",
            blockers=blockers,
        )

    payload = _activation_payload(snapshot)
    try:
        challenge = issue_owner_step_up_challenge(
            db,
            credential_id=credential_id,
            purpose=CANARY_OWNER_STEP_UP_PURPOSE,
            payload=payload,
            ttl=timedelta(seconds=CANARY_V1_CHALLENGE_TTL_SECONDS),
        )
    except OwnerStepUpError as exc:
        raise CanaryOwnerActivationRejected("owner step-up challenge rejected") from exc
    return _issued(
        challenge,
        snapshot_hash=snapshot.snapshot_hash,
        payload=payload,
    )


def _assert_challenge_binding(
    db: Session,
    *,
    credential_id: object,
    challenge_id: uuid.UUID,
) -> OwnerStepUpChallenge:
    credential = db.get(DeviceCredential, credential_id)
    if credential is None or credential.revoked_at is not None:
        raise CanaryOwnerActivationRejected("active device credential is required")
    challenge = db.get(OwnerStepUpChallenge, challenge_id)
    if challenge is None:
        raise CanaryOwnerActivationRejected("owner step-up challenge does not exist")
    if (
        challenge.device_id != credential.device_id
        or challenge.purpose != CANARY_OWNER_STEP_UP_PURPOSE
    ):
        raise CanaryOwnerActivationRejected("owner step-up challenge scope mismatch")
    return challenge


def _existing_result(
    db: Session,
    *,
    challenge_id: uuid.UUID,
    snapshot_hash: str,
) -> CanaryOwnerActivationResult | None:
    row = db.execute(
        select(AuditEvent)
        .where(
            AuditEvent.action == _AUDIT_ACTION,
            AuditEvent.subject == str(challenge_id),
        )
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    after = row.after_json if isinstance(row.after_json, dict) else {}
    if after.get("snapshot_hash") != snapshot_hash:
        raise CanaryOwnerActivationRejected("challenge was already bound to another snapshot")
    try:
        mode = ExecutionLifecycleMode(str(after["mode"]))
        status = str(after["status"])
        blockers_raw = after.get("blockers", [])
        if not isinstance(blockers_raw, list):
            raise TypeError
        blockers = tuple(str(item) for item in blockers_raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise CanaryOwnerActivationRejected("stored Canary activation result is invalid") from exc
    return CanaryOwnerActivationResult(
        challenge_id=challenge_id,
        snapshot_hash=snapshot_hash,
        status=status,
        mode=mode,
        blockers=blockers,
    )


def _record_result(
    db: Session,
    *,
    challenge_id: uuid.UUID,
    snapshot_hash: str,
    payload_hash: str,
    device_id: str,
    owner_key_fingerprint: str,
    verified_at: object,
    status: str,
    mode: ExecutionLifecycleMode,
    blockers: tuple[str, ...],
) -> CanaryOwnerActivationResult:
    db.add(
        AuditEvent(
            actor="owner",
            action=_AUDIT_ACTION,
            subject=str(challenge_id),
            detail=status,
            before_json={
                "from_mode": ExecutionLifecycleMode.SANDBOX.value,
                "target_mode": ExecutionLifecycleMode.CANARY.value,
            },
            after_json={
                "status": status,
                "mode": mode.value,
                "snapshot_hash": snapshot_hash,
                "payload_hash": payload_hash,
                "device_id": device_id,
                "owner_key_fingerprint": owner_key_fingerprint,
                "verified_at": str(verified_at),
                "blockers": list(blockers),
            },
        )
    )
    db.flush()
    return CanaryOwnerActivationResult(
        challenge_id=challenge_id,
        snapshot_hash=snapshot_hash,
        status=status,
        mode=mode,
        blockers=blockers,
    )


def confirm_canary_owner_activation(
    db: Session,
    *,
    credential_id: object,
    snapshot_hash: str,
    challenge_id: uuid.UUID,
    signature_b64: str,
    context_provider: RuntimeContextProvider,
) -> CanaryOwnerActivationResult:
    """Consume exact owner proof, recheck every gate, then atomically enter CANARY.

    The global execution lock serializes this decision with kill-switch changes,
    lifecycle mutations and provider submits.  A durable audit result is checked
    before signature verification so a network retry of an already completed
    confirmation returns the same outcome without replaying authority.
    """

    normalized_snapshot = str(snapshot_hash).strip().lower()
    if _HEX64.fullmatch(normalized_snapshot) is None:
        raise CanaryOwnerActivationRejected("snapshot_hash must be a SHA-256 hex digest")
    try:
        normalized_challenge = uuid.UUID(str(challenge_id))
    except (ValueError, AttributeError) as exc:
        raise CanaryOwnerActivationRejected("challenge_id must be a UUID") from exc

    with execution_control_lock(db):
        _assert_challenge_binding(
            db,
            credential_id=credential_id,
            challenge_id=normalized_challenge,
        )
        prior = _existing_result(
            db,
            challenge_id=normalized_challenge,
            snapshot_hash=normalized_snapshot,
        )
        if prior is not None:
            return prior

        try:
            receipt = verify_owner_step_up_signature(
                db,
                credential_id=credential_id,
                challenge_id=normalized_challenge,
                signature_b64=signature_b64,
            )
        except OwnerStepUpError as exc:
            raise CanaryOwnerActivationRejected("owner step-up proof rejected") from exc
        if receipt.purpose != CANARY_OWNER_STEP_UP_PURPOSE:
            raise CanaryOwnerActivationRejected("owner step-up purpose mismatch")

        blockers: list[str] = []
        snapshot: CanaryPolicySnapshot | None = None
        payload: dict[str, object] | None = None
        try:
            snapshot = _snapshot(db, normalized_snapshot)
            payload = _activation_payload(snapshot)
        except CanaryOwnerActivationRejected:
            blockers.append("CANARY_POLICY_NOT_FOUND_OR_INVALID")

        if payload is not None and receipt.payload_hash != _payload_hash(payload):
            blockers.append("CANARY_OWNER_PAYLOAD_STALE")

        readiness = None
        if snapshot is not None:
            try:
                readiness = build_canary_activation_readiness(
                    db,
                    snapshot_hash=snapshot.snapshot_hash,
                    context_provider=context_provider,
                )
            except CanaryActivationReadinessError:
                blockers.append("CANARY_READINESS_UNAVAILABLE")
        if readiness is not None:
            for blocker in _readiness_blockers(readiness):
                if blocker not in blockers:
                    blockers.append(blocker)

        for blocker in _kill_switch_blockers(db):
            if blocker not in blockers:
                blockers.append(blocker)

        current_mode = get_execution_mode(db).mode
        if current_mode is not ExecutionLifecycleMode.SANDBOX:
            if "EXECUTION_MODE_NOT_SANDBOX" not in blockers:
                blockers.append("EXECUTION_MODE_NOT_SANDBOX")

        if blockers:
            return _record_result(
                db,
                challenge_id=normalized_challenge,
                snapshot_hash=normalized_snapshot,
                payload_hash=receipt.payload_hash,
                device_id=receipt.device_id,
                owner_key_fingerprint=receipt.owner_key_fingerprint,
                verified_at=receipt.verified_at,
                status="BLOCKED",
                mode=current_mode,
                blockers=tuple(blockers),
            )

        assert snapshot is not None and payload is not None
        authorization_detail = dict(payload)
        authorization_detail.update(
            {
                "owner_step_up_challenge_id": str(receipt.challenge_id),
                "owner_step_up_device_id": receipt.device_id,
                "owner_key_fingerprint": receipt.owner_key_fingerprint,
                "owner_step_up_payload_hash": receipt.payload_hash,
                "owner_step_up_verified_at": str(receipt.verified_at),
            }
        )
        mode = _change_execution_mode_locked(
            db,
            target=ExecutionLifecycleMode.CANARY,
            actor="owner",
            reason="Activate owner-approved Canary v1",
            authorization=ModeChangeAuthorization(
                allowed=True,
                actor="canary-owner-step-up",
                reason="fresh biometric owner proof bound to exact Canary v1 snapshot",
                detail_json=authorization_detail,
            ),
        ).mode
        return _record_result(
            db,
            challenge_id=normalized_challenge,
            snapshot_hash=normalized_snapshot,
            payload_hash=receipt.payload_hash,
            device_id=receipt.device_id,
            owner_key_fingerprint=receipt.owner_key_fingerprint,
            verified_at=receipt.verified_at,
            status="APPLIED",
            mode=mode,
            blockers=(),
        )


__all__ = [
    "CANARY_OWNER_STEP_UP_PURPOSE",
    "CanaryOwnerActivationChallenge",
    "CanaryOwnerActivationRejected",
    "CanaryOwnerActivationResult",
    "confirm_canary_owner_activation",
    "issue_canary_owner_activation_challenge",
]
