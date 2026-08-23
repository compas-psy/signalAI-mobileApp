from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

from Crypto.PublicKey import ECC
from sqlalchemy import func, select

from app.models.device import DeviceCredential, DeviceOwnerKey, OwnerStepUpChallenge
from app.owner_step_up import issue_owner_step_up_challenge, validate_owner_public_key_spki_b64


def test_live_self_test_challenge_is_reused_instead_of_creating_unbounded_rows(session):
    now = datetime(2026, 8, 23, 19, 45, tzinfo=UTC)
    private = ECC.generate(curve="P-256")
    public_b64 = base64.b64encode(
        private.public_key().export_key(format="DER")
    ).decode("ascii")
    canonical, fingerprint = validate_owner_public_key_spki_b64(public_b64)

    credential = DeviceCredential(
        device_id="owner-rate-bound-device-0001",
        generation=1,
        token_verifier="a" * 64,
        issued_request_hash="b" * 64,
        metadata_json={"platform": "android"},
        issued_at=now,
    )
    session.add(credential)
    session.flush()
    session.add(
        DeviceOwnerKey(
            device_id=credential.device_id,
            algorithm="ECDSA_P256_SHA256",
            public_key_spki_b64=canonical,
            public_key_sha256=fingerprint,
            enrolled_pairing_session_verifier="c" * 64,
            enrolled_at=now,
        )
    )
    session.flush()

    first = issue_owner_step_up_challenge(
        session,
        credential_id=credential.id,
        purpose="OWNER_STEP_UP_SELF_TEST",
        payload={"device_id": credential.device_id, "device_generation": 1},
        ttl=timedelta(seconds=60),
        now=now,
    )
    second = issue_owner_step_up_challenge(
        session,
        credential_id=credential.id,
        purpose="OWNER_STEP_UP_SELF_TEST",
        payload={"device_id": credential.device_id, "device_generation": 1},
        ttl=timedelta(seconds=60),
        now=now + timedelta(seconds=1),
    )

    assert second.challenge_id == first.challenge_id
    assert second.message == first.message
    assert session.scalar(
        select(func.count(OwnerStepUpChallenge.id)).where(
            OwnerStepUpChallenge.device_id == credential.device_id,
            OwnerStepUpChallenge.purpose == "OWNER_STEP_UP_SELF_TEST",
        )
    ) == 1
