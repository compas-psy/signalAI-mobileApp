from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from Crypto.PublicKey import ECC
from sqlalchemy import select

from app.device_enrollment import (
    BootstrapPairingSession,
    DeviceEnrollmentError,
    pair_device,
    revoke_device,
    rotate_device,
)
from app.models.device import DeviceCredential, DeviceOwnerKey
from app.owner_step_up import validate_owner_public_key_spki_b64


def _spki(curve: str = "P-256") -> str:
    key = ECC.generate(curve=curve)
    der = key.public_key().export_key(format="DER")
    return base64.b64encode(der).decode("ascii")


def _pairing(seed: str, *, minutes: int = 10) -> BootstrapPairingSession:
    return BootstrapPairingSession(
        session_id=f"{seed:_<43}"[:43],
        verifier=(seed.encode("utf-8").hex() + "0" * 64)[:64],
        expires_at=datetime.now(UTC) + timedelta(minutes=minutes),
        max_uses=1,
    )


def test_validator_accepts_only_p256_spki():
    normalized, fingerprint = validate_owner_public_key_spki_b64(_spki("P-256"))

    assert normalized
    assert len(fingerprint) == 64
    assert fingerprint == fingerprint.lower()

    with pytest.raises(DeviceEnrollmentError):
        validate_owner_public_key_spki_b64(_spki("P-384"))
    with pytest.raises(DeviceEnrollmentError):
        validate_owner_public_key_spki_b64("not-base64-or-der")


def test_pairing_enrolls_owner_key_in_same_transaction(session):
    spki = _spki()
    issued = pair_device(
        session,
        device_id="owner-step-device-0001",
        metadata={"platform": "android"},
        idempotency_key="owner-step-pair-0001",
        pairing_session=_pairing("pair-a"),
        owner_public_key_spki_b64=spki,
    )

    credential = session.scalar(
        select(DeviceCredential).where(DeviceCredential.device_id == issued.device_id)
    )
    owner_key = session.scalar(
        select(DeviceOwnerKey).where(DeviceOwnerKey.device_id == issued.device_id)
    )
    assert credential is not None
    assert owner_key is not None
    assert owner_key.algorithm == "ECDSA_P256_SHA256"
    assert owner_key.public_key_spki_b64 == spki
    assert owner_key.revoked_at is None


def test_invalid_owner_key_mints_neither_bearer_nor_owner_key(session):
    with pytest.raises(DeviceEnrollmentError):
        pair_device(
            session,
            device_id="owner-step-device-0002",
            metadata={},
            idempotency_key="owner-step-pair-0002",
            pairing_session=_pairing("pair-b"),
            owner_public_key_spki_b64=_spki("P-384"),
        )

    assert session.scalar(
        select(DeviceCredential.id).where(
            DeviceCredential.device_id == "owner-step-device-0002"
        )
    ) is None
    assert session.scalar(
        select(DeviceOwnerKey.id).where(
            DeviceOwnerKey.device_id == "owner-step-device-0002"
        )
    ) is None


def test_bearer_rotation_does_not_replace_owner_key(session):
    spki = _spki()
    issued = pair_device(
        session,
        device_id="owner-step-device-0003",
        metadata={},
        idempotency_key="owner-step-pair-0003",
        pairing_session=_pairing("pair-c"),
        owner_public_key_spki_b64=spki,
    )
    credential = session.scalar(
        select(DeviceCredential).where(
            DeviceCredential.device_id == issued.device_id,
            DeviceCredential.revoked_at.is_(None),
        )
    )
    assert credential is not None
    owner_key = session.scalar(
        select(DeviceOwnerKey).where(
            DeviceOwnerKey.device_id == issued.device_id,
            DeviceOwnerKey.revoked_at.is_(None),
        )
    )
    assert owner_key is not None
    original_key_id = owner_key.id

    rotate_device(
        session,
        credential_id=credential.id,
        idempotency_key="owner-step-rotate-0003",
    )
    active_key = session.scalar(
        select(DeviceOwnerKey).where(
            DeviceOwnerKey.device_id == issued.device_id,
            DeviceOwnerKey.revoked_at.is_(None),
        )
    )
    assert active_key is not None
    assert active_key.id == original_key_id
    assert active_key.public_key_spki_b64 == spki


def test_new_owner_pairing_can_replace_key_only_after_device_is_revoked(session):
    first_spki = _spki()
    first = pair_device(
        session,
        device_id="owner-step-device-0004",
        metadata={},
        idempotency_key="owner-step-pair-0004a",
        pairing_session=_pairing("pair-d"),
        owner_public_key_spki_b64=first_spki,
    )
    first_credential = session.scalar(
        select(DeviceCredential).where(
            DeviceCredential.device_id == first.device_id,
            DeviceCredential.revoked_at.is_(None),
        )
    )
    assert first_credential is not None
    revoke_device(session, credential_id=first_credential.id)

    second_spki = _spki()
    second = pair_device(
        session,
        device_id=first.device_id,
        metadata={"platform": "android"},
        idempotency_key="owner-step-pair-0004b",
        pairing_session=_pairing("pair-e"),
        owner_public_key_spki_b64=second_spki,
    )

    keys = list(
        session.scalars(
            select(DeviceOwnerKey)
            .where(DeviceOwnerKey.device_id == first.device_id)
            .order_by(DeviceOwnerKey.enrolled_at, DeviceOwnerKey.id)
        )
    )
    assert second.generation == 2
    assert len(keys) == 2
    assert sum(key.revoked_at is None for key in keys) == 1
    active = next(key for key in keys if key.revoked_at is None)
    assert active.public_key_spki_b64 == second_spki


def test_pairing_without_owner_key_remains_backwards_compatible(session):
    issued = pair_device(
        session,
        device_id="owner-step-device-0005",
        metadata={},
        idempotency_key="owner-step-pair-0005",
        pairing_session=_pairing("pair-f"),
    )

    assert issued.generation == 1
    assert session.scalar(
        select(DeviceOwnerKey.id).where(DeviceOwnerKey.device_id == issued.device_id)
    ) is None
