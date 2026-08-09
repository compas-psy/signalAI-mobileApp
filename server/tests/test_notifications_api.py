from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from tests.conftest import DEVICE_HEADERS


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as c:
        yield c
    app.dependency_overrides.clear()


def test_notifications_are_device_authenticated():
    with TestClient(app) as client:
        response = client.get("/api/v1/notifications")
    assert response.status_code == 401


def test_manual_test_event_is_created_on_server_and_read_from_outbox(client):
    response = client.post("/api/v1/notifications/test")
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    event = body["event"]
    assert event["kind"] == "SYSTEM"
    assert event["title"] == "SignalAI · тест с сервера"
    assert "VPS" in event["body"]

    readback = client.get(
        "/api/v1/notifications",
        params={"after": max(0, int(event["id"]) - 1)},
    )
    assert readback.status_code == 200
    rows = readback.json()["events"]
    assert any(row["id"] == event["id"] for row in rows)


def test_sse_route_is_registered_as_owner_api():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/v1/notifications/stream" in paths
