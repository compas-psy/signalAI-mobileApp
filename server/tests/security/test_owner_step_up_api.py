from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta

from Crypto.Hash import SHA256
from Crypto.PublicKey import ECC
from Crypto.Signature import DSS
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.v1.owner_step_up import router
from app.db import get_db
from app.device_enrollment import (
    BootstrapPairingSession,
    authenticate_active_device,
    pair_device,
)
from app.models.device import OwnerStepUpChallenge
from app.security import DeviceTokenMiddleware


def _pairing(seed: str) -> BootstrapPairingSession:
    return BootstrapPairingSession(
        session_id=f"{seed:_<43}"[:43],
        verifier=(seed.encode("utf-8").hex() + "0" * 64)[:64],
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        max_uses=1,
    )


def _pair_owner(session, suffix: str):
    private = ECC.generate(curve="P-256")
    public_b64 = base64.b64encode(
        private.public_key().export_key(format="DER")
    ).decode("ascii")
    issued = pair_device(
        session,
        device_id=f"owner-api-device-{suffix}",
        metadata={"platform": "android"},
        idempotency_key=f"owner-api-pairing-{suffix}",
        pairing_session=_pairing(f"owner-api-{suffix}"),
        owner_public_key_spki_b64=public_b64,
    )
    return private, issued


def _client(session) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        DeviceTokenMiddleware,
        authenticate=lambda token: authenticate_active_device(session, token),
    )
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app)


def _sign(private, message: str) -> str:
    signature = DSS.new(private, "fips-186-3", encoding="der").sign(
        SHA256.new(message.encode("utf-8"))
    )
    return base64.b64encode(signature).decode("ascii")


def test_self_test_api_binds_server_owned_purpose_and_payload(session):
    _private, issued = _pair_owner(session, "0001")
    client = _client(session)
    headers = {"Authorization": f"Bearer {issued.token}"}

    response = client.post(
        "/api/v1/owner-step-up/self-test/challenge",
        headers=headers,
        json={
            "purpose": "SANDBOX_TO_CANARY",
            "payload": {"capital": "attacker-controlled"},
        },
    )

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    body = response.json()
    assert body["purpose"] == "OWNER_STEP_UP_SELF_TEST"
    canonical = json.dumps(
        {"device_generation": 1, "device_id": issued.device_id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert body["payload_hash"] == hashlib.sha256(canonical).hexdigest()
    assert issued.device_id in body["message"]
    assert "SANDBOX_TO_CANARY" not in body["message"]
    assert "attacker-controlled" not in body["message"]


def test_self_test_api_verifies_signature_once_but_returns_no_authority(session):
    private, issued = _pair_owner(session, "0002")
    client = _client(session)
    headers = {"Authorization": f"Bearer {issued.token}"}

    challenge = client.post(
        "/api/v1/owner-step-up/self-test/challenge",
        headers=headers,
    )
    assert challenge.status_code == 201
    challenge_body = challenge.json()
    signature = _sign(private, challenge_body["message"])

    verified = client.post(
        "/api/v1/owner-step-up/self-test/verify",
        headers=headers,
        json={
            "challenge_id": challenge_body["challenge_id"],
            "signature_b64": signature,
        },
    )

    assert verified.status_code == 200
    assert verified.headers["cache-control"] == "no-store"
    assert verified.headers["pragma"] == "no-cache"
    receipt = verified.json()
    assert receipt["challenge_id"] == challenge_body["challenge_id"]
    assert receipt["device_id"] == issued.device_id
    assert receipt["purpose"] == "OWNER_STEP_UP_SELF_TEST"
    assert receipt["payload_hash"] == challenge_body["payload_hash"]
    assert "authorized" not in receipt
    assert "execution_mode" not in receipt
    assert "canary" not in receipt

    replay = client.post(
        "/api/v1/owner-step-up/self-test/verify",
        headers=headers,
        json={
            "challenge_id": challenge_body["challenge_id"],
            "signature_b64": signature,
        },
    )
    assert replay.status_code == 403


def test_self_test_api_wrong_signature_fails_without_consuming_challenge(session):
    _private, issued = _pair_owner(session, "0003")
    wrong_private = ECC.generate(curve="P-256")
    client = _client(session)
    headers = {"Authorization": f"Bearer {issued.token}"}
    challenge = client.post(
        "/api/v1/owner-step-up/self-test/challenge",
        headers=headers,
    ).json()

    denied = client.post(
        "/api/v1/owner-step-up/self-test/verify",
        headers=headers,
        json={
            "challenge_id": challenge["challenge_id"],
            "signature_b64": _sign(wrong_private, challenge["message"]),
        },
    )

    assert denied.status_code == 403
    row = session.scalar(
        select(OwnerStepUpChallenge).where(
            OwnerStepUpChallenge.id == challenge["challenge_id"]
        )
    )
    assert row is not None
    assert row.consumed_at is None


def test_self_test_api_requires_active_device_bearer(session):
    client = _client(session)
    assert client.post(
        "/api/v1/owner-step-up/self-test/challenge"
    ).status_code == 401
