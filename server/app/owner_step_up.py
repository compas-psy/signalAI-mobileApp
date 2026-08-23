"""Cryptographic owner-presence primitives with no execution authority.

The server stores only the public half of a P-256 key.  A bearer-authenticated
owner device may request a bounded challenge, but a proof receipt is emitted
only after a valid signature from the exact enrolled key.  Receipts are facts,
not authorization: this module never changes execution mode, provider state,
capital limits, or promotion state.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from Crypto.Hash import SHA256
from Crypto.PublicKey import ECC
from Crypto.Signature import DSS
from sqlalchemy import select
from sqlalchemy.orm import Session

from .device_enrollment import DeviceEnrollmentError
from .models.device import DeviceCredential, DeviceOwnerKey, OwnerStepUpChallenge

_OWNER_KEY_ALGORITHM = "ECDSA_P256_SHA256"
_ALLOWED_P256_CURVES = frozenset({"NIST P-256", "P-256", "prime256v1", "secp256r1"})
_PURPOSE_RE = re.compile(r"[A-Z0-9_]{1,64}\Z")
_MAX_PAYLOAD_BYTES = 4096
_MAX_TTL = timedelta(minutes=5)
_MAX_SIGNATURE_B64_LENGTH = 256
_MESSAGE_DOMAIN = "SIGNALAI_OWNER_STEP_UP_V1"


class OwnerStepUpError(ValueError):
    """Fail-closed owner-presence validation error with no oracle detail."""


@dataclass(frozen=True, slots=True)
class ValidatedOwnerPublicKey:
    spki_b64: str
    fingerprint_sha256: str


@dataclass(frozen=True, slots=True)
class IssuedOwnerStepUpChallenge:
    challenge_id: uuid.UUID
    device_id: str
    owner_key_fingerprint: str
    purpose: str
    payload_hash: str
    message: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OwnerStepUpProofReceipt:
    challenge_id: uuid.UUID
    device_id: str
    owner_key_fingerprint: str
    purpose: str
    payload_hash: str
    verified_at: datetime


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OwnerStepUpError("owner step-up proof is invalid")
    return value.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_payload(payload: object) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise OwnerStepUpError("owner step-up proof is invalid")
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise OwnerStepUpError("owner step-up proof is invalid") from exc
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise OwnerStepUpError("owner step-up proof is invalid")
    return encoded.decode("utf-8"), hashlib.sha256(encoded).hexdigest()


def _iso_utc(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _challenge_message(row: OwnerStepUpChallenge) -> str:
    body = {
        "challenge_id": str(row.id),
        "device_id": row.device_id,
        "expires_at": _iso_utc(row.expires_at),
        "issued_at": _iso_utc(row.issued_at),
        "nonce_hex": row.nonce_hex,
        "payload_hash": row.payload_hash,
        "purpose": row.purpose,
    }
    canonical = json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return f"{_MESSAGE_DOMAIN}\n{canonical}"


def _issued_challenge(
    row: OwnerStepUpChallenge,
    *,
    owner_key_fingerprint: str,
) -> IssuedOwnerStepUpChallenge:
    return IssuedOwnerStepUpChallenge(
        challenge_id=row.id,
        device_id=row.device_id,
        owner_key_fingerprint=owner_key_fingerprint,
        purpose=row.purpose,
        payload_hash=row.payload_hash,
        message=_challenge_message(row),
        expires_at=row.expires_at,
    )


def validate_owner_public_key_spki_b64(value: object) -> tuple[str, str]:
    """Validate one canonical public P-256 SubjectPublicKeyInfo value.

    Private EC material, other curves, non-DER encodings and non-canonical
    base64 are rejected.  The returned fingerprint is over the canonical DER
    SPKI and is safe to use as a non-secret identity reference.
    """
    if not isinstance(value, str) or not 80 <= len(value) <= 256:
        raise DeviceEnrollmentError("owner public key is invalid")
    try:
        der = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DeviceEnrollmentError("owner public key is invalid") from exc
    try:
        key = ECC.import_key(der)
    except (ValueError, TypeError, IndexError) as exc:
        raise DeviceEnrollmentError("owner public key is invalid") from exc
    if key.has_private() or str(key.curve) not in _ALLOWED_P256_CURVES:
        raise DeviceEnrollmentError("owner public key is invalid")
    try:
        canonical_der = key.public_key().export_key(format="DER")
    except (ValueError, TypeError) as exc:
        raise DeviceEnrollmentError("owner public key is invalid") from exc
    if not isinstance(canonical_der, bytes) or canonical_der != der:
        raise DeviceEnrollmentError("owner public key is invalid")
    canonical_b64 = base64.b64encode(canonical_der).decode("ascii")
    if canonical_b64 != value:
        raise DeviceEnrollmentError("owner public key is invalid")
    return canonical_b64, hashlib.sha256(canonical_der).hexdigest()


def replace_owner_key_during_pairing(
    session: Session,
    *,
    device_id: str,
    pairing_session_verifier: str,
    validated_key: tuple[str, str],
    now: datetime,
) -> DeviceOwnerKey:
    """Monotonically replace an owner key inside an authorized pairing flow.

    The database trigger permits only the first revocation transition on an
    existing key.  This helper does not authenticate pairing by itself and is
    deliberately not exposed through a bearer-authenticated API.
    """
    spki_b64, fingerprint = validated_key
    active = session.scalar(
        select(DeviceOwnerKey)
        .where(
            DeviceOwnerKey.device_id == device_id,
            DeviceOwnerKey.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if active is not None:
        active.revoked_at = now
        session.flush()
    key = DeviceOwnerKey(
        device_id=device_id,
        algorithm=_OWNER_KEY_ALGORITHM,
        public_key_spki_b64=spki_b64,
        public_key_sha256=fingerprint,
        enrolled_pairing_session_verifier=pairing_session_verifier,
        enrolled_at=now,
    )
    session.add(key)
    session.flush()
    return key


def issue_owner_step_up_challenge(
    session: Session,
    *,
    credential_id: object,
    purpose: object,
    payload: object,
    ttl: timedelta,
    now: datetime | None = None,
) -> IssuedOwnerStepUpChallenge:
    """Persist or reuse one bounded single-use request for an active owner key.

    The active owner-key row is locked before looking for a pending challenge.
    That serializes concurrent issuance for the same device.  Reusing the exact
    live device/key/purpose/payload challenge bounds an authenticated bearer
    that cannot produce the biometric signature to at most one pending row per
    TTL instead of allowing unbounded write amplification.
    """
    if not isinstance(purpose, str) or _PURPOSE_RE.fullmatch(purpose) is None:
        raise OwnerStepUpError("owner step-up proof is invalid")
    if not isinstance(ttl, timedelta) or ttl <= timedelta(0) or ttl > _MAX_TTL:
        raise OwnerStepUpError("owner step-up proof is invalid")
    _canonical, payload_hash = _canonical_payload(payload)
    issued_at = _utc(now or _now())

    credential = session.scalar(
        select(DeviceCredential)
        .where(
            DeviceCredential.id == credential_id,
            DeviceCredential.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if credential is None:
        raise OwnerStepUpError("owner step-up proof is invalid")
    owner_key = session.scalar(
        select(DeviceOwnerKey)
        .where(
            DeviceOwnerKey.device_id == credential.device_id,
            DeviceOwnerKey.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if owner_key is None or owner_key.algorithm != _OWNER_KEY_ALGORITHM:
        raise OwnerStepUpError("owner step-up proof is invalid")
    try:
        _stored_spki, fingerprint = validate_owner_public_key_spki_b64(
            owner_key.public_key_spki_b64
        )
    except DeviceEnrollmentError as exc:
        raise OwnerStepUpError("owner step-up proof is invalid") from exc
    if fingerprint != owner_key.public_key_sha256:
        raise OwnerStepUpError("owner step-up proof is invalid")

    existing = session.scalar(
        select(OwnerStepUpChallenge)
        .where(
            OwnerStepUpChallenge.device_id == credential.device_id,
            OwnerStepUpChallenge.owner_key_id == owner_key.id,
            OwnerStepUpChallenge.purpose == purpose,
            OwnerStepUpChallenge.payload_hash == payload_hash,
            OwnerStepUpChallenge.consumed_at.is_(None),
            OwnerStepUpChallenge.expires_at > issued_at,
        )
        .order_by(OwnerStepUpChallenge.expires_at.desc())
        .limit(1)
    )
    if existing is not None:
        return _issued_challenge(
            existing,
            owner_key_fingerprint=owner_key.public_key_sha256,
        )

    row = OwnerStepUpChallenge(
        device_id=credential.device_id,
        owner_key_id=owner_key.id,
        purpose=purpose,
        payload_hash=payload_hash,
        nonce_hex=secrets.token_hex(32),
        issued_at=issued_at,
        expires_at=issued_at + ttl,
    )
    session.add(row)
    session.flush()
    return _issued_challenge(
        row,
        owner_key_fingerprint=owner_key.public_key_sha256,
    )


def _decode_signature(value: object) -> bytes:
    if not isinstance(value, str) or not 8 <= len(value) <= _MAX_SIGNATURE_B64_LENGTH:
        raise OwnerStepUpError("owner step-up proof is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise OwnerStepUpError("owner step-up proof is invalid") from exc
    if base64.b64encode(decoded).decode("ascii") != value or not 8 <= len(decoded) <= 128:
        raise OwnerStepUpError("owner step-up proof is invalid")
    return decoded


def verify_owner_step_up_signature(
    session: Session,
    *,
    credential_id: object,
    challenge_id: object,
    signature_b64: object,
    now: datetime | None = None,
) -> OwnerStepUpProofReceipt:
    """Verify and consume one challenge, returning a non-authorizing fact."""
    try:
        challenge_uuid = (
            challenge_id
            if isinstance(challenge_id, uuid.UUID)
            else uuid.UUID(str(challenge_id))
        )
    except (ValueError, TypeError, AttributeError) as exc:
        raise OwnerStepUpError("owner step-up proof is invalid") from exc
    signature = _decode_signature(signature_b64)
    verified_at = _utc(now or _now())

    credential = session.scalar(
        select(DeviceCredential).where(
            DeviceCredential.id == credential_id,
            DeviceCredential.revoked_at.is_(None),
        )
    )
    if credential is None:
        raise OwnerStepUpError("owner step-up proof is invalid")
    row = session.scalar(
        select(OwnerStepUpChallenge)
        .where(OwnerStepUpChallenge.id == challenge_uuid)
        .with_for_update()
    )
    if (
        row is None
        or row.device_id != credential.device_id
        or row.consumed_at is not None
        or verified_at < _utc(row.issued_at)
        or verified_at >= _utc(row.expires_at)
    ):
        raise OwnerStepUpError("owner step-up proof is invalid")

    owner_key = session.scalar(
        select(DeviceOwnerKey).where(
            DeviceOwnerKey.id == row.owner_key_id,
            DeviceOwnerKey.device_id == row.device_id,
            DeviceOwnerKey.revoked_at.is_(None),
        )
    )
    if owner_key is None or owner_key.algorithm != _OWNER_KEY_ALGORITHM:
        raise OwnerStepUpError("owner step-up proof is invalid")
    try:
        canonical_spki, fingerprint = validate_owner_public_key_spki_b64(
            owner_key.public_key_spki_b64
        )
    except DeviceEnrollmentError as exc:
        raise OwnerStepUpError("owner step-up proof is invalid") from exc
    if fingerprint != owner_key.public_key_sha256:
        raise OwnerStepUpError("owner step-up proof is invalid")

    try:
        public_key = ECC.import_key(base64.b64decode(canonical_spki, validate=True))
        verifier = DSS.new(public_key, "fips-186-3", encoding="der")
        verifier.verify(
            SHA256.new(_challenge_message(row).encode("utf-8")),
            signature,
        )
    except (ValueError, TypeError, IndexError, binascii.Error) as exc:
        raise OwnerStepUpError("owner step-up proof is invalid") from exc

    row.consumed_at = verified_at
    session.flush()
    return OwnerStepUpProofReceipt(
        challenge_id=row.id,
        device_id=row.device_id,
        owner_key_fingerprint=owner_key.public_key_sha256,
        purpose=row.purpose,
        payload_hash=row.payload_hash,
        verified_at=verified_at,
    )


__all__ = [
    "IssuedOwnerStepUpChallenge",
    "OwnerStepUpError",
    "OwnerStepUpProofReceipt",
    "ValidatedOwnerPublicKey",
    "issue_owner_step_up_challenge",
    "replace_owner_key_during_pairing",
    "validate_owner_public_key_spki_b64",
    "verify_owner_step_up_signature",
]
