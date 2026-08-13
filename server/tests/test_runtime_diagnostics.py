"""P0 runtime diagnostics: correlation and aggregate-only owner snapshot."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from tests.conftest import DEVICE_HEADERS


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as value:
        yield value
    app.dependency_overrides.clear()


def _uuid(value: str) -> UUID:
    return UUID(value)


def test_request_id_is_generated_for_public_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert _uuid(response.headers["x-request-id"])


def test_valid_incoming_request_id_is_preserved(client):
    request_id = str(uuid4())
    response = client.get("/health", headers={"X-Request-ID": request_id})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == request_id


def test_request_id_survives_fail_closed_authentication(session):
    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app) as anonymous:
            response = anonymous.get("/api/v1/diagnostics/runtime")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert _uuid(response.headers["x-request-id"])


def test_runtime_diagnostics_empty_state_is_aggregate_only(client):
    response = client.get("/api/v1/diagnostics/runtime")
    assert response.status_code == 200
    body = response.json()

    assert _uuid(body["request_id"])
    assert body["request_id"] == response.headers["x-request-id"]
    assert body["generated_at"].endswith("Z") or "+00:00" in body["generated_at"]
    assert body["ideas"] == {
        "total": 0,
        "by_status": {},
        "latest_signal_at": None,
    }
    assert body["paper"] == {
        "total": 0,
        "by_status": {},
        "live": 0,
        "unreconciled_live": 0,
        "oldest_live_reconciled_at": None,
    }
    assert body["notifications"] == {
        "total": 0,
        "latest_id": None,
        "latest_created_at": None,
    }

    serialized = response.text.lower()
    for forbidden in (
        "authorization",
        "bearer",
        "instrument_id",
        "dedup_key",
        "payload",
        "title",
        "body",
    ):
        assert forbidden not in serialized
