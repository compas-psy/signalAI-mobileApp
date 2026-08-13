"""P0 runtime diagnostics: aggregate-only owner snapshot."""

from __future__ import annotations

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


def test_runtime_diagnostics_empty_state_is_aggregate_only(client):
    response = client.get("/api/v1/control/runtime")
    assert response.status_code == 200
    body = response.json()

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
        "instrument_id",
        "dedup_key",
        "payload",
        "title",
        "body",
    ):
        assert forbidden not in serialized
