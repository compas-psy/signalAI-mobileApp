from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.execution.enums import ExecutionLifecycleMode
from app.main import app
from tests.conftest import DEVICE_HEADERS


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_canary_challenge_uses_authenticated_device_and_returns_exact_signing_message(
    client, monkeypatch
):
    import app.api.v1.owner_step_up as api
    from app.execution.canary_owner_activation import CanaryOwnerActivationChallenge

    challenge_id = uuid.uuid4()
    seen = {}

    def fake_issue(db, *, credential_id, snapshot_hash, context_provider):
        seen["credential_id"] = credential_id
        seen["snapshot_hash"] = snapshot_hash
        seen["context"] = context_provider()
        return CanaryOwnerActivationChallenge(
            challenge_id=challenge_id,
            snapshot_hash=snapshot_hash,
            device_id="ci-device-enrollment-fixture",
            owner_key_fingerprint="f" * 64,
            purpose="CANARY_V1_ACTIVATE",
            message="SIGNALAI_OWNER_STEP_UP_V1\nexact-message",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            payload={"capital_amount": "100", "capital_currency": "USDC"},
        )

    monkeypatch.setattr(api, "issue_canary_owner_activation_challenge", fake_issue)
    monkeypatch.setenv("SIGNALAI_SOURCE_SHA", "a" * 40)

    response = client.post(
        "/api/v1/execution/canary/activation/challenge",
        json={"snapshot_hash": "b" * 64},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["challenge_id"] == str(challenge_id)
    assert body["message"] == "SIGNALAI_OWNER_STEP_UP_V1\nexact-message"
    assert body["payload"] == {"capital_amount": "100", "capital_currency": "USDC"}
    assert seen["credential_id"] is not None
    assert seen["snapshot_hash"] == "b" * 64
    assert seen["context"].source_sha == "a" * 40


def test_canary_confirm_uses_authenticated_device_and_never_accepts_runtime_proof_from_client(
    client, monkeypatch
):
    import app.api.v1.owner_step_up as api
    from app.execution.canary_owner_activation import CanaryOwnerActivationResult

    challenge_id = uuid.uuid4()
    seen = {}

    def fake_confirm(
        db,
        *,
        credential_id,
        snapshot_hash,
        challenge_id,
        signature_b64,
        context_provider,
    ):
        seen.update(
            credential_id=credential_id,
            snapshot_hash=snapshot_hash,
            challenge_id=challenge_id,
            signature_b64=signature_b64,
            context=context_provider(),
        )
        return CanaryOwnerActivationResult(
            challenge_id=challenge_id,
            snapshot_hash=snapshot_hash,
            status="BLOCKED",
            mode=ExecutionLifecycleMode.SANDBOX,
            blockers=("RISK_PAPER_ONLY",),
        )

    monkeypatch.setattr(api, "confirm_canary_owner_activation", fake_confirm)
    monkeypatch.setenv("SIGNALAI_SOURCE_SHA", "c" * 40)

    response = client.post(
        "/api/v1/execution/canary/activation/confirm",
        json={
            "snapshot_hash": "d" * 64,
            "challenge_id": str(challenge_id),
            "signature_b64": "MEUCIQDtestsignature",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "BLOCKED"
    assert body["mode"] == "SANDBOX"
    assert body["blockers"] == ["RISK_PAPER_ONLY"]
    assert seen["credential_id"] is not None
    assert seen["context"].source_sha == "c" * 40

    rejected_extra = client.post(
        "/api/v1/execution/canary/activation/confirm",
        json={
            "snapshot_hash": "d" * 64,
            "challenge_id": str(challenge_id),
            "signature_b64": "MEUCIQDtestsignature",
            "source_sha": "e" * 40,
            "paper_only": False,
        },
    )
    assert rejected_extra.status_code == 422


def test_canary_activation_rejections_are_sanitized_as_conflict(client, monkeypatch):
    import app.api.v1.owner_step_up as api
    from app.execution.canary_owner_activation import CanaryOwnerActivationRejected

    def fail(*args, **kwargs):
        raise CanaryOwnerActivationRejected(
            "Canary activation is not ready for owner step-up",
            blockers=("RISK_PAPER_ONLY", "CANARY_EVIDENCE_MISSING:shadow"),
        )

    monkeypatch.setattr(api, "issue_canary_owner_activation_challenge", fail)
    response = client.post(
        "/api/v1/execution/canary/activation/challenge",
        json={"snapshot_hash": "f" * 64},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "message": "Canary activation is not ready for owner step-up",
        "blockers": ["RISK_PAPER_ONLY", "CANARY_EVIDENCE_MISSING:shadow"],
    }
