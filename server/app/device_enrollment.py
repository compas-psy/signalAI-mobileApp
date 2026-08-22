"""Token generation and verifier-only device credential primitives."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models.device import DeviceCredential, DevicePairingSession

_DEVICE_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")
_METADATA_LIMITS = {"label": 64, "platform": 24, "app_version": 32}
_DEVICE_ID_RE = re.compile(r"[A-Za-z0-9_-]{16,64}\Z")
_IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9._:-]{16,128}\Z")
_DEVICE_ENROLLMENT_LOCK = 7_531_075


class DeviceEnrollmentError(ValueError):
    pass


class DeviceEnrollmentReplay(DeviceEnrollmentError):
    pass


class DeviceEnrollmentConflict(DeviceEnrollmentError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedDeviceCredential:
    device_id: str
    generation: int
    token: str
    issued_at: datetime


@dataclass(frozen=True, slots=True)
class BootstrapPairingSession:
    """Ephemeral configured capability; only its verifier is persisted."""

    session_id: str
    verifier: str
    expires_at: datetime
    max_uses: int


def issue_device_token() -> str:
    """Return a one-time 256-bit bearer; callers must never persist it raw."""
    return secrets.token_urlsafe(32)


def token_verifier(token: str) -> str:
    if not isinstance(token, str) or _DEVICE_TOKEN_RE.fullmatch(token) is None:
        raise ValueError("device token is malformed")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _pairing_session_verifier(session_id: str) -> str:
    if _DEVICE_TOKEN_RE.fullmatch(session_id) is None:
        raise DeviceEnrollmentError("pairing session is invalid")
    return hashlib.sha256(
        b"signalai:device-pairing-session:v1\x00" + session_id.encode("utf-8")
    ).hexdigest()


def normalize_device_metadata(raw: object) -> dict[str, str]:
    """Accept only a small non-secret device descriptor for audit display."""
    if not isinstance(raw, dict) or set(raw) - set(_METADATA_LIMITS):
        raise ValueError("device metadata is invalid")
    normalized: dict[str, str] = {}
    for key, maximum in _METADATA_LIMITS.items():
        value = raw.get(key)
        if value is None:
            continue
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value.strip()) > maximum
        ):
            raise ValueError("device metadata is invalid")
        normalized[key] = value.strip()
    return normalized


def _device_id(value: object) -> str:
    if not isinstance(value, str) or _DEVICE_ID_RE.fullmatch(value) is None:
        raise DeviceEnrollmentError("device id is invalid")
    return value


def _request_hash(value: object) -> str:
    if not isinstance(value, str) or _IDEMPOTENCY_KEY_RE.fullmatch(value) is None:
        raise DeviceEnrollmentError("idempotency key is invalid")
    return hashlib.sha256(
        b"signalai:device-enrollment:v1\x00" + value.encode()
    ).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def bootstrap_pairing_session_config(
    *, now: datetime | None = None
) -> BootstrapPairingSession:
    """Read the separate bootstrap-pairing capability, failing closed.

    The raw value is intentionally process-local.  Database rows retain a
    domain-separated verifier, expiry, and monotonic use count only.
    """
    session_id = os.environ.get("SIGNALAI_DEVICE_PAIRING_SESSION_ID", "").strip()
    if not session_id:
        raise DeviceEnrollmentError("pairing session is not configured")
    try:
        verifier = _pairing_session_verifier(session_id)
    except DeviceEnrollmentError as exc:
        raise DeviceEnrollmentError("pairing session is invalid") from exc

    raw_expiry = os.environ.get("SIGNALAI_DEVICE_PAIRING_EXPIRES_AT", "").strip()
    if not raw_expiry:
        raise DeviceEnrollmentError("pairing session expiry is not configured")
    try:
        expires_at = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            raise ValueError("timezone required")
        expires_at = expires_at.astimezone(UTC)
    except ValueError as exc:
        raise DeviceEnrollmentError("pairing session expiry is invalid") from exc
    if expires_at <= (now or _now()):
        raise DeviceEnrollmentError("pairing session is expired")

    raw_max_uses = os.environ.get("SIGNALAI_DEVICE_PAIRING_MAX_USES", "1").strip()
    try:
        max_uses = int(raw_max_uses)
    except ValueError as exc:
        raise DeviceEnrollmentError("pairing session uses are invalid") from exc
    if not 1 <= max_uses <= 16:
        raise DeviceEnrollmentError("pairing session uses are invalid")
    return BootstrapPairingSession(
        session_id=session_id,
        verifier=verifier,
        expires_at=expires_at,
        max_uses=max_uses,
    )


def _max_active_devices() -> int:
    raw = os.environ.get("SIGNALAI_MAX_ACTIVE_DEVICES", "5").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise DeviceEnrollmentError("active device limit is invalid") from exc
    if not 1 <= value <= 16:
        raise DeviceEnrollmentError("active device limit is invalid")
    return value


def authenticate_active_device(session: Session, token: str) -> DeviceCredential | None:
    """Return one active credential and record only a timestamp, never its bearer."""
    try:
        verifier = token_verifier(token)
    except ValueError:
        return None
    credential = session.scalar(
        select(DeviceCredential).where(
            DeviceCredential.token_verifier == verifier,
            DeviceCredential.revoked_at.is_(None),
        )
    )
    if credential is None:
        return None
    credential.last_authenticated_at = _now()
    session.flush()
    return credential


def pair_device(
    session: Session,
    *,
    device_id: object,
    metadata: object,
    idempotency_key: object,
    pairing_session: BootstrapPairingSession,
) -> IssuedDeviceCredential:
    """Issue exactly one raw bearer for a new pairing request.

    Replaying a completed request is deliberately not replayable as a token:
    the raw secret exists only in the first response and is never persisted.
    """
    device_id = _device_id(device_id)
    metadata = normalize_device_metadata(metadata)
    request_hash = _request_hash(idempotency_key)
    # Pairing uses one transaction-scoped PostgreSQL advisory lock.  It makes
    # the owner-device ceiling durable even when two bootstrap requests race
    # in different API workers; row locks alone cannot protect an empty slot.
    session.execute(select(func.pg_advisory_xact_lock(_DEVICE_ENROLLMENT_LOCK)))
    now = _now()
    if pairing_session.expires_at <= now:
        raise DeviceEnrollmentError("pairing session is expired")
    if session.scalar(
        select(DeviceCredential.id).where(
            DeviceCredential.issued_request_hash == request_hash
        )
    ) is not None:
        raise DeviceEnrollmentReplay("pairing request was already completed")

    # Pairing an existing ID used to revoke its current owner bearer.  That
    # turns a leaked bootstrap capability into a denial-of-service primitive;
    # device rotation and owner-authenticated lost-device revoke are explicit
    # lifecycle operations instead.
    known_credential = session.scalar(
        select(DeviceCredential)
        .where(DeviceCredential.device_id == device_id)
        .with_for_update()
        .limit(1)
    )
    if known_credential is not None:
        raise DeviceEnrollmentConflict("device is already enrolled")

    durable_session = session.scalar(
        select(DevicePairingSession)
        .where(DevicePairingSession.session_verifier == pairing_session.verifier)
        .with_for_update()
    )
    if durable_session is None:
        durable_session = DevicePairingSession(
            session_verifier=pairing_session.verifier,
            expires_at=pairing_session.expires_at,
            max_uses=pairing_session.max_uses,
            uses=0,
        )
        session.add(durable_session)
        session.flush()
    elif (
        durable_session.expires_at != pairing_session.expires_at
        or durable_session.max_uses != pairing_session.max_uses
    ):
        # Operators must explicitly provision a new random session instead of
        # extending/rebounding a value that may already have leaked.
        raise DeviceEnrollmentConflict("pairing session configuration changed")
    if durable_session.uses >= durable_session.max_uses:
        raise DeviceEnrollmentConflict("pairing session is exhausted")

    active_credentials = list(
        session.scalars(
            select(DeviceCredential)
            .where(DeviceCredential.revoked_at.is_(None))
            .with_for_update()
        )
    )
    if len(active_credentials) >= _max_active_devices():
        raise DeviceEnrollmentError("active device limit is reached")
    token = issue_device_token()
    credential = DeviceCredential(
        device_id=device_id,
        generation=1,
        token_verifier=token_verifier(token),
        issued_request_hash=request_hash,
        metadata_json=metadata,
        issued_at=now,
    )
    session.add(credential)
    durable_session.uses += 1
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise DeviceEnrollmentConflict("device enrollment changed concurrently") from exc
    return IssuedDeviceCredential(
        device_id=device_id, generation=1, token=token, issued_at=now
    )


def rotate_device(
    session: Session,
    *,
    credential_id: object,
    idempotency_key: object,
) -> IssuedDeviceCredential:
    """Revoke the authenticated generation before issuing its replacement."""
    request_hash = _request_hash(idempotency_key)
    current = session.scalar(
        select(DeviceCredential)
        .where(DeviceCredential.id == credential_id)
        .with_for_update()
    )
    if current is None or current.revoked_at is not None:
        raise DeviceEnrollmentConflict("device credential is no longer active")
    if session.scalar(
        select(DeviceCredential.id).where(
            DeviceCredential.issued_request_hash == request_hash
        )
    ) is not None:
        raise DeviceEnrollmentReplay("rotation request was already completed")
    now = _now()
    current.revoked_at = now
    token = issue_device_token()
    replacement = DeviceCredential(
        device_id=current.device_id,
        generation=current.generation + 1,
        token_verifier=token_verifier(token),
        issued_request_hash=request_hash,
        metadata_json=dict(current.metadata_json or {}),
        issued_at=now,
    )
    session.add(replacement)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise DeviceEnrollmentConflict("device credential rotation conflicted") from exc
    return IssuedDeviceCredential(
        device_id=replacement.device_id,
        generation=replacement.generation,
        token=token,
        issued_at=now,
    )


def revoke_device(session: Session, *, credential_id: object) -> None:
    credential = session.scalar(
        select(DeviceCredential)
        .where(DeviceCredential.id == credential_id)
        .with_for_update()
    )
    if credential is None or credential.revoked_at is not None:
        raise DeviceEnrollmentConflict("device credential is no longer active")
    credential.revoked_at = _now()
    session.flush()


def revoke_device_token(session: Session, *, token: object) -> bool:
    """Idempotently revoke the exact supplied bearer without logging it."""
    try:
        verifier = token_verifier(token)  # type: ignore[arg-type]
    except ValueError as exc:
        raise DeviceEnrollmentError("device credential is not authorized") from exc
    credential = session.scalar(
        select(DeviceCredential)
        .where(DeviceCredential.token_verifier == verifier)
        .with_for_update()
    )
    if credential is None:
        raise DeviceEnrollmentError("device credential is not authorized")
    if credential.revoked_at is not None:
        return False
    credential.revoked_at = _now()
    session.flush()
    return True


def revoke_lost_device(
    session: Session,
    *,
    actor_credential_id: object,
    target_device_id: object,
    target_generation: object,
) -> bool:
    """Let one active owner device revoke another specified generation."""
    if not isinstance(target_generation, int) or target_generation < 1:
        raise DeviceEnrollmentError("target device generation is invalid")
    target_device_id = _device_id(target_device_id)
    actor = session.scalar(
        select(DeviceCredential)
        .where(DeviceCredential.id == actor_credential_id)
        .with_for_update()
    )
    if actor is None or actor.revoked_at is not None:
        raise DeviceEnrollmentConflict("owner device is no longer active")
    target = session.scalar(
        select(DeviceCredential)
        .where(
            DeviceCredential.device_id == target_device_id,
            DeviceCredential.generation == target_generation,
        )
        .with_for_update()
    )
    if target is None:
        raise DeviceEnrollmentConflict("target device is not enrolled")
    if target.id == actor.id:
        raise DeviceEnrollmentConflict("use self revocation for this device")
    if target.revoked_at is not None:
        return False
    target.revoked_at = _now()
    session.flush()
    return True


__all__ = [
    "DeviceEnrollmentConflict",
    "DeviceEnrollmentError",
    "DeviceEnrollmentReplay",
    "BootstrapPairingSession",
    "IssuedDeviceCredential",
    "authenticate_active_device",
    "bootstrap_pairing_session_config",
    "issue_device_token",
    "normalize_device_metadata",
    "pair_device",
    "revoke_device",
    "revoke_device_token",
    "revoke_lost_device",
    "rotate_device",
    "token_verifier",
]
