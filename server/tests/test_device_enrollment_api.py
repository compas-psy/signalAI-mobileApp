"""End-to-end contract for bootstrap pairing and active device credentials."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.v1.device_enrollment import router
from app.db import get_db
from app.device_enrollment import authenticate_active_device
from app.models import DeviceCredential, DevicePairingSession
from app.security import DeviceTokenMiddleware
from tests.conftest import DEVICE_TOKEN, PAIRING_SESSION_ID

BOOTSTRAP_TOKEN = "b" * 43


@pytest.fixture(autouse=True)
def distinct_bootstrap_token(configured_device_token, monkeypatch):
    """Bootstrap capability must never be the pre-enrolled business bearer."""
    monkeypatch.setenv("SIGNALAI_DEVICE_TOKEN", BOOTSTRAP_TOKEN)


def _client(session) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        DeviceTokenMiddleware,
        authenticate=lambda token: authenticate_active_device(session, token),
    )
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: session

    @app.get("/api/v1/business")
    def business(request: Request):
        return {
            "device_id": request.state.device_id,
            "generation": request.state.device_generation,
        }

    return TestClient(app)


def _pair_headers(key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {BOOTSTRAP_TOKEN}",
        "X-Pairing-Session-Id": PAIRING_SESSION_ID,
        "X-Idempotency-Key": key,
    }


def test_pair_rotate_and_revoke_are_active_token_only_and_replay_safe(session):
    client = _client(session)
    pair_headers = _pair_headers("pairing-request-0001")
    payload = {
        "device_id": "android-test-device-01",
        "metadata": {
            "label": "Owner phone",
            "platform": "android",
            "app_version": "1.0.0",
        },
    }

    paired = client.post(
        "/api/v1/device-enrollment/pair",
        headers=pair_headers,
        json=payload,
    )
    assert paired.status_code == 201
    assert paired.headers["cache-control"] == "no-store"
    issued = paired.json()["device_token"]
    assert issued != BOOTSTRAP_TOKEN
    assert len(issued) >= 43

    # The completed request cannot mint another bearer, including after a
    # transport retry.  The database holds only its verifier/hash.
    replay = client.post(
        "/api/v1/device-enrollment/pair",
        headers=pair_headers,
        json=payload,
    )
    assert replay.status_code == 409
    rows = list(
        session.scalars(
            select(DeviceCredential).where(
                DeviceCredential.device_id == payload["device_id"]
            )
        )
    )
    assert len(rows) == 1
    assert all(issued not in row.token_verifier for row in rows)
    assert all(issued not in str(row.metadata_json) for row in rows)

    bootstrap_business = client.get(
        "/api/v1/business", headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}
    )
    assert bootstrap_business.status_code == 401
    active_business = client.get(
        "/api/v1/business", headers={"Authorization": f"Bearer {issued}"}
    )
    assert active_business.json() == {"device_id": payload["device_id"], "generation": 1}

    rotated = client.post(
        "/api/v1/device-enrollment/rotate",
        headers={
            "Authorization": f"Bearer {issued}",
            "X-Idempotency-Key": "rotation-request-0001",
        },
    )
    assert rotated.status_code == 200
    replacement = rotated.json()["device_token"]
    assert replacement != issued

    # Rotation commits one replacement generation and immediately removes the
    # old bearer from the only business-API admission path.
    assert client.get(
        "/api/v1/business", headers={"Authorization": f"Bearer {issued}"}
    ).status_code == 401
    assert client.get(
        "/api/v1/business", headers={"Authorization": f"Bearer {replacement}"}
    ).json()["generation"] == 2

    revoked = client.post(
        "/api/v1/device-enrollment/revoke",
        headers={"Authorization": f"Bearer {replacement}"},
    )
    assert revoked.status_code == 200
    assert revoked.json() == {"status": "revoked"}
    assert client.get(
        "/api/v1/business", headers={"Authorization": f"Bearer {replacement}"}
    ).status_code == 401
    already_revoked = client.post(
        "/api/v1/device-enrollment/revoke",
        headers={"Authorization": f"Bearer {replacement}"},
    )
    assert already_revoked.status_code == 200
    assert already_revoked.json() == {"status": "already_revoked"}


def test_pair_rejects_missing_bootstrap_or_unbounded_metadata(session, monkeypatch):
    client = _client(session)
    headers = {"X-Idempotency-Key": "pairing-request-0002"}
    payload = {
        "device_id": "android-test-device-02",
        "metadata": {"label": "x" * 65},
    }

    monkeypatch.delenv("SIGNALAI_DEVICE_TOKEN", raising=False)
    assert (
        client.post(
            "/api/v1/device-enrollment/pair",
            headers=headers,
            json=payload,
        ).status_code
        == 503
    )

    monkeypatch.setenv("SIGNALAI_DEVICE_TOKEN", BOOTSTRAP_TOKEN)
    response = client.post(
        "/api/v1/device-enrollment/pair",
        headers={
            **headers,
            "Authorization": f"Bearer {BOOTSTRAP_TOKEN}",
            "X-Pairing-Session-Id": PAIRING_SESSION_ID,
        },
        json=payload,
    )
    assert response.status_code == 422


def test_pair_requires_durable_bounded_session_and_never_replaces_known_device(
    session, monkeypatch
):
    client = _client(session)
    payload = {
        "device_id": "android-session-device-01",
        "metadata": {"platform": "android"},
    }

    # The static bootstrap secret by itself fails closed.
    missing_session = client.post(
        "/api/v1/device-enrollment/pair",
        headers={
            "Authorization": f"Bearer {BOOTSTRAP_TOKEN}",
            "X-Idempotency-Key": "session-pairing-key-01",
        },
        json=payload,
    )
    assert missing_session.status_code == 503

    # An attempt to pair an existing device cannot silently revoke it or spend
    # the owner-provisioned pairing capability.
    known = client.post(
        "/api/v1/device-enrollment/pair",
        headers=_pair_headers("session-pairing-key-03"),
        json={
            **payload,
            "device_id": "ci-device-enrollment-fixture",
        },
    )
    assert known.status_code == 409
    assert client.get(
        "/api/v1/business", headers={"Authorization": f"Bearer {DEVICE_TOKEN}"}
    ).status_code == 200

    monkeypatch.setenv("SIGNALAI_DEVICE_PAIRING_MAX_USES", "1")
    paired = client.post(
        "/api/v1/device-enrollment/pair",
        headers=_pair_headers("session-pairing-key-01"),
        json=payload,
    )
    assert paired.status_code == 201
    session_row = session.scalar(select(DevicePairingSession))
    assert session_row is not None
    assert session_row.uses == 1
    assert PAIRING_SESSION_ID not in session_row.session_verifier

    exhausted = client.post(
        "/api/v1/device-enrollment/pair",
        headers=_pair_headers("session-pairing-key-02"),
        json={**payload, "device_id": "android-session-device-02"},
    )
    assert exhausted.status_code == 409


def test_active_owner_can_revoke_a_lost_peer_but_not_use_its_bearer(session):
    client = _client(session)

    def pair(device_id: str, key: str) -> str:
        response = client.post(
            "/api/v1/device-enrollment/pair",
            headers=_pair_headers(key),
            json={"device_id": device_id, "metadata": {"platform": "android"}},
        )
        assert response.status_code == 201
        return response.json()["device_token"]

    actor = pair("android-owner-device-01", "lost-device-pairing-key-01")
    target = pair("android-lost-device-0001", "lost-device-pairing-key-02")
    target_row = session.scalar(
        select(DeviceCredential).where(
            DeviceCredential.device_id == "android-lost-device-0001",
            DeviceCredential.revoked_at.is_(None),
        )
    )
    assert target_row is not None

    revoked = client.post(
        "/api/v1/device-enrollment/revoke-lost",
        headers={"Authorization": f"Bearer {actor}"},
        json={
            "device_id": target_row.device_id,
            "generation": target_row.generation,
        },
    )
    assert revoked.status_code == 204
    assert client.get(
        "/api/v1/business", headers={"Authorization": f"Bearer {target}"}
    ).status_code == 401
    assert client.get(
        "/api/v1/business", headers={"Authorization": f"Bearer {actor}"}
    ).status_code == 200


def test_pair_has_a_bounded_active_device_ceiling(session, monkeypatch):
    for credential in session.scalars(
        select(DeviceCredential).where(DeviceCredential.revoked_at.is_(None))
    ):
        credential.revoked_at = credential.issued_at
    session.flush()
    monkeypatch.setenv("SIGNALAI_MAX_ACTIVE_DEVICES", "1")
    client = _client(session)

    def pair(device_id: str, key: str):
        return client.post(
            "/api/v1/device-enrollment/pair",
            headers={
                **_pair_headers(key),
            },
            json={"device_id": device_id, "metadata": {"platform": "android"}},
        )

    assert pair("android-bounded-device-01", "bounded-pairing-key-01").status_code == 201
    assert pair("android-bounded-device-02", "bounded-pairing-key-02").status_code == 422
