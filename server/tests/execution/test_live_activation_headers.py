from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from tests.conftest import DEVICE_HEADERS


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_live_confirm_accepts_mobile_x_idempotency_key(client):
    preview = client.post("/api/v1/execution/live/preview")
    assert preview.status_code == 200
    preview_hash = preview.json()["preview_hash"]

    response = client.post(
        "/api/v1/execution/live/confirm",
        headers={"X-Idempotency-Key": "mobile-live-confirm-1"},
        json={
            "preview_hash": preview_hash,
            "owner_confirmed": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["idempotency_key"] == "mobile-live-confirm-1"
    assert body["status"] == "BLOCKED"
    assert body["mode"] == "PAPER"
