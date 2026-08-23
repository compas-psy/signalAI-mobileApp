from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.integration_secrets import BY_SLOT, load_secret
from app.main import app
from tests.conftest import DEVICE_HEADERS


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as value:
        yield value
    app.dependency_overrides.clear()


def test_tinvest_sandbox_trade_is_write_only_server_slot(client, session):
    spec = BY_SLOT["tinvest_sandbox_trade"]
    assert spec.venue == "TINVEST"
    assert spec.environment == "sandbox"
    assert spec.fields == ("token",)
    assert spec.required is False

    secret = "sandbox-token-that-must-never-be-returned"
    saved = client.put(
        "/api/v1/integrations/tinvest_sandbox_trade",
        json={"values": {"token": secret}},
    )
    assert saved.status_code == 200
    assert saved.json()["configured"] is True
    assert saved.json()["environment"] == "sandbox"
    assert secret not in saved.text

    listed = client.get("/api/v1/integrations")
    assert listed.status_code == 200
    row = next(
        item for item in listed.json() if item["slot"] == "tinvest_sandbox_trade"
    )
    assert row["configured"] is True
    assert row["fields"] == ["token"]
    assert secret not in listed.text
    assert load_secret(session, "tinvest_sandbox_trade") == {"token": secret}


def test_tinvest_sandbox_trade_requires_exact_token_field(client):
    missing = client.put(
        "/api/v1/integrations/tinvest_sandbox_trade",
        json={"values": {}},
    )
    assert missing.status_code == 422

    extra = client.put(
        "/api/v1/integrations/tinvest_sandbox_trade",
        json={"values": {"token": "abc", "account": "should-not-exist"}},
    )
    assert extra.status_code == 422
