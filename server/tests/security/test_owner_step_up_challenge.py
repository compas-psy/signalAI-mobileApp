from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from Crypto.Hash import SHA256
from Crypto.PublicKey import ECC
from Crypto.Signature import DSS
from sqlalchemy import select, text

from app import owner_step_up
from app.device_enrollment import BootstrapPairingSession, pair_device, revoke_device
from app.models.device import DeviceCredential, DeviceOwnerKey, OwnerStepUpChallenge


def _pairing(seed: str) -> BootstrapPairingSession:
    return BootstrapPairingSession(
        session_id=f"{seed:_<43}"[:43],
        verifier=(seed.encode("utf-8").hex() + "0" * 64)[:64],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        max_uses=1,
    )


def _enroll(session, suffix: str):
    private = ECC.generate(curve="P-256")
    public_der = private.public_key().export_key(format="DER")
    public_b64 = base64.b64encode(public_der).decode("ascii")
    issued = pair_device(
        session,
        device_id=f"owner-challenge-device-{suffix}",
        metadata={"platform": "android"},
        idempotency_key=f"owner-challenge-pair-{suffix}",
        pairing_session=_pairing(f"pair-{suffix}"),
        owner_public_key_spki_b64=public_b64,
    )
    credential = session.scalar(
        select(DeviceCredential).where(
            DeviceCredential.device_id == issued.device_id,
            DeviceCredential.revoked_at.is_(None),
        )
    )
    owner_key = session.scalar(
        select(DeviceOwnerKey).where(
            DeviceOwnerKey.device_id == issued.device_id,
            DeviceOwnerKey.revoked_at.is_(None),
        )
    )
    assert credential is not None
    assert owner_key is not None
    return private, credential, owner_key


def _sign(private, message: str) -> str:
    signer = DSS.new(private, "fips-186-3", encoding="der")
    signature = signer.sign(SHA256.new(message.encode("utf-8")))
    return base64.b64encode(signature).decode("ascii")


def test_challenge_binds_exact_canonical_payload_and_owner_key(session):
    _private, credential, owner_key = _enroll(session, "0001")
    now = datetime(2026, 8, 23, 11, 10, tzinfo=UTC)

    challenge = owner_step_up.issue_owner_step_up_challenge(
        session,
        credential_id=credential.id,
        purpose="OWNER_STEP_UP_SELF_TEST",
        payload={"b": 2, "a": 1},
        ttl=timedelta(seconds=45),
        now=now,
    )

    expected_payload_hash = hashlib.sha256(b'{"a":1,"b":2}').hexdigest()
    row = session.get(OwnerStepUpChallenge, challenge.challenge_id)
    assert row is not None
    assert row.device_id == credential.device_id
    assert row.owner_key_id == owner_key.id
    assert row.purpose == "OWNER_STEP_UP_SELF_TEST"
    assert row.payload_hash == expected_payload_hash
    assert row.nonce_hex in challenge.message
    assert str(challenge.challenge_id) in challenge.message
    assert credential.device_id in challenge.message
    assert expected_payload_hash in challenge.message
    assert challenge.expires_at == now + timedelta(seconds=45)


def test_challenge_rejects_inactive_device_or_missing_owner_key(session):
    _private, key_revoked_credential, owner_key = _enroll(session, "0002")
    owner_key.revoked_at = datetime.now(UTC)
    session.flush()

    with pytest.raises(owner_step_up.OwnerStepUpError):
        owner_step_up.issue_owner_step_up_challenge(
            session,
            credential_id=key_revoked_credential.id,
            purpose="OWNER_STEP_UP_SELF_TEST",
            payload={"device_id": key_revoked_credential.device_id},
            ttl=timedelta(seconds=30),
        )

    _private2, credential_revoked, _owner_key2 = _enroll(session, "0007")
    revoke_device(session, credential_id=credential_revoked.id)
    with pytest.raises(owner_step_up.OwnerStepUpError):
        owner_step_up.issue_owner_step_up_challenge(
            session,
            credential_id=credential_revoked.id,
            purpose="OWNER_STEP_UP_SELF_TEST",
            payload={"device_id": credential_revoked.device_id},
            ttl=timedelta(seconds=30),
        )


def test_valid_signature_consumes_challenge_once_and_returns_non_authorizing_receipt(session):
    private, credential, owner_key = _enroll(session, "0003")
    now = datetime(2026, 8, 23, 11, 20, tzinfo=UTC)
    before_risk = session.execute(
        text("SELECT execution_mode, kill_switch, kill_switch_level FROM risk_state WHERE id = 1")
    ).first()
    challenge = owner_step_up.issue_owner_step_up_challenge(
        session,
        credential_id=credential.id,
        purpose="OWNER_STEP_UP_SELF_TEST",
        payload={"device_id": credential.device_id},
        ttl=timedelta(seconds=60),
        now=now,
    )
    signature = _sign(private, challenge.message)

    receipt = owner_step_up.verify_owner_step_up_signature(
        session,
        credential_id=credential.id,
        challenge_id=challenge.challenge_id,
        signature_b64=signature,
        now=now + timedelta(seconds=1),
    )

    row = session.get(OwnerStepUpChallenge, challenge.challenge_id)
    assert row is not None
    assert row.consumed_at == now + timedelta(seconds=1)
    assert receipt.challenge_id == challenge.challenge_id
    assert receipt.device_id == credential.device_id
    assert receipt.owner_key_fingerprint == owner_key.public_key_sha256
    assert receipt.purpose == "OWNER_STEP_UP_SELF_TEST"
    assert receipt.payload_hash == row.payload_hash
    assert receipt.verified_at == row.consumed_at
    assert not hasattr(receipt, "authorized")
    assert not hasattr(receipt, "execution_mode")
    assert session.execute(
        text("SELECT execution_mode, kill_switch, kill_switch_level FROM risk_state WHERE id = 1")
    ).first() == before_risk

    with pytest.raises(owner_step_up.OwnerStepUpError):
        owner_step_up.verify_owner_step_up_signature(
            session,
            credential_id=credential.id,
            challenge_id=challenge.challenge_id,
            signature_b64=signature,
            now=now + timedelta(seconds=2),
        )


def test_wrong_key_altered_message_wrong_device_and_expiry_fail_without_consumption(session):
    private, credential, _owner_key = _enroll(session, "0004")
    other_private, other_credential, _other_owner_key = _enroll(session, "0005")
    now = datetime(2026, 8, 23, 11, 30, tzinfo=UTC)

    challenge = owner_step_up.issue_owner_step_up_challenge(
        session,
        credential_id=credential.id,
        purpose="OWNER_STEP_UP_SELF_TEST",
        payload={"device_id": credential.device_id},
        ttl=timedelta(seconds=20),
        now=now,
    )
    wrong_key_signature = _sign(other_private, challenge.message)
    altered_signature = _sign(private, challenge.message + "\nTAMPERED")

    for actor_id, signature, verify_now in (
        (credential.id, wrong_key_signature, now + timedelta(seconds=1)),
        (credential.id, altered_signature, now + timedelta(seconds=2)),
        (other_credential.id, _sign(private, challenge.message), now + timedelta(seconds=3)),
        (credential.id, _sign(private, challenge.message), now + timedelta(seconds=21)),
    ):
        with pytest.raises(owner_step_up.OwnerStepUpError):
            owner_step_up.verify_owner_step_up_signature(
                session,
                credential_id=actor_id,
                challenge_id=challenge.challenge_id,
                signature_b64=signature,
                now=verify_now,
            )
        row = session.get(OwnerStepUpChallenge, challenge.challenge_id)
        assert row is not None
        assert row.consumed_at is None


def test_challenge_inputs_are_bounded_and_fail_closed(session):
    _private, credential, _owner_key = _enroll(session, "0006")

    for purpose, payload, ttl in (
        ("lowercase", {}, timedelta(seconds=30)),
        ("X" * 65, {}, timedelta(seconds=30)),
        ("OWNER_STEP_UP_SELF_TEST", {"bad": float("nan")}, timedelta(seconds=30)),
        ("OWNER_STEP_UP_SELF_TEST", {}, timedelta(seconds=0)),
        ("OWNER_STEP_UP_SELF_TEST", {}, timedelta(hours=2)),
    ):
        with pytest.raises(owner_step_up.OwnerStepUpError):
            owner_step_up.issue_owner_step_up_challenge(
                session,
                credential_id=credential.id,
                purpose=purpose,
                payload=payload,
                ttl=ttl,
            )

    assert session.scalar(
        select(OwnerStepUpChallenge.id).where(
            OwnerStepUpChallenge.device_id == credential.device_id
        )
    ) is None
