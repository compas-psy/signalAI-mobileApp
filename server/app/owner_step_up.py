"""Cryptographic owner-presence primitives with no execution authority.

This module intentionally contains only public-key validation/enrollment helpers
at this stage.  A device bearer is never sufficient to create or replace an
owner key; callers must already hold the short-lived owner pairing capability.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass
from datetime import datetime

from Crypto.PublicKey import ECC
from sqlalchemy import select
from sqlalchemy.orm import Session

from .device_enrollment import DeviceEnrollmentError
from .models.device import DeviceOwnerKey

_OWNER_KEY_ALGORITHM = "ECDSA_P256_SHA256"
_ALLOWED_P256_CURVES = frozenset({"NIST P-256", "P-256", "prime256v1", "secp256r1"})


@dataclass(frozen=True, slots=True)
class ValidatedOwnerPublicKey:
    spki_b64: str
    fingerprint_sha256: str


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


__all__ = [
    "ValidatedOwnerPublicKey",
    "replace_owner_key_during_pairing",
    "validate_owner_public_key_spki_b64",
]
