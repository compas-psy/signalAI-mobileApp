from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.device import DeviceOwnerKey, OwnerStepUpChallenge


_KEY_SPKI = "M" * 120
_KEY_HASH = "a" * 64
_SESSION_VERIFIER = "b" * 64


def _key(*, device_id: str = "owner-step-up-device-0001") -> DeviceOwnerKey:
    return DeviceOwnerKey(
        device_id=device_id,
        algorithm="ECDSA_P256_SHA256",
        public_key_spki_b64=_KEY_SPKI,
        public_key_sha256=_KEY_HASH,
        enrolled_pairing_session_verifier=_SESSION_VERIFIER,
    )


def test_owner_step_up_tables_exist_with_expected_contract(session):
    key = _key()
    session.add(key)
    session.flush()

    now = datetime.now(UTC)
    challenge = OwnerStepUpChallenge(
        device_id=key.device_id,
        owner_key_id=key.id,
        purpose="OWNER_STEP_UP_SELF_TEST",
        payload_hash="c" * 64,
        nonce_hex="d" * 64,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    session.add(challenge)
    session.flush()

    assert key.id is not None
    assert challenge.id is not None
    assert challenge.consumed_at is None


def test_only_one_active_owner_key_per_device(session):
    first = _key()
    session.add(first)
    session.flush()

    session.add(
        DeviceOwnerKey(
            device_id=first.device_id,
            algorithm="ECDSA_P256_SHA256",
            public_key_spki_b64="N" * 120,
            public_key_sha256="e" * 64,
            enrolled_pairing_session_verifier="f" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_challenge_scope_is_immutable_but_consumption_is_monotonic(session):
    key = _key(device_id="owner-step-up-device-0002")
    session.add(key)
    session.flush()
    now = datetime.now(UTC)
    challenge = OwnerStepUpChallenge(
        device_id=key.device_id,
        owner_key_id=key.id,
        purpose="OWNER_STEP_UP_SELF_TEST",
        payload_hash="1" * 64,
        nonce_hex="2" * 64,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    session.add(challenge)
    session.flush()

    with pytest.raises(DBAPIError):
        session.execute(
            text(
                "UPDATE owner_step_up_challenges "
                "SET purpose = 'TAMPERED' WHERE id = :id"
            ),
            {"id": challenge.id},
        )
    session.rollback()


def test_challenge_can_be_consumed_once_but_not_rewritten_afterward(session):
    key = _key(device_id="owner-step-up-device-0003")
    session.add(key)
    session.flush()
    now = datetime.now(UTC)
    challenge = OwnerStepUpChallenge(
        device_id=key.device_id,
        owner_key_id=key.id,
        purpose="OWNER_STEP_UP_SELF_TEST",
        payload_hash="3" * 64,
        nonce_hex="4" * 64,
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    session.add(challenge)
    session.flush()

    consumed = now + timedelta(seconds=1)
    session.execute(
        text(
            "UPDATE owner_step_up_challenges "
            "SET consumed_at = :consumed WHERE id = :id"
        ),
        {"consumed": consumed, "id": challenge.id},
    )
    session.flush()

    with pytest.raises(DBAPIError):
        session.execute(
            text(
                "UPDATE owner_step_up_challenges "
                "SET consumed_at = :consumed WHERE id = :id"
            ),
            {"consumed": consumed + timedelta(seconds=1), "id": challenge.id},
        )
