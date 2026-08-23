from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.integration_secrets import load_secret
from app.main import app
from tests.conftest import DEVICE_HEADERS


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as c:
        yield c
    app.dependency_overrides.clear()


def test_integrations_list_has_server_owned_tinvest_and_bybit_without_secret_values(client):
    response = client.get("/api/v1/integrations")
    assert response.status_code == 200
    body = response.json()
    slots = {item["slot"] for item in body}
    assert {
        "tinvest_invest_read",
        "tinvest_sandbox_trade",
        "tinvest_trade",
        "bybit_read",
        "bybit_testnet_trade",
        "bybit_trade",
    } <= slots
    # The legacy ambiguous slot name is deliberately not an alias: sandbox
    # execution has one exact server-owned credential identity.
    assert "tinvest_sandbox" not in slots
    assert all(item["configured"] is False for item in body)
    assert "token" not in response.text.lower() or "fields" in response.text

    by_slot = {item["slot"]: item for item in body}
    assert by_slot["tinvest_invest_read"]["required"] is True
    assert by_slot["tinvest_sandbox_trade"]["required"] is False
    assert by_slot["tinvest_sandbox_trade"]["environment"] == "sandbox"
    assert by_slot["tinvest_trade"]["required"] is True
    assert by_slot["bybit_testnet_trade"]["required"] is False
    assert by_slot["bybit_trade"]["required"] is True


def test_tinvest_server_token_is_write_only_and_encrypted_at_rest(client, session):
    secret = "t.example-super-secret-token-123456789"
    response = client.put(
        "/api/v1/integrations/tinvest_invest_read",
        json={"values": {"token": secret}},
    )
    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert secret not in response.text

    status = client.get("/api/v1/integrations").json()
    invest_read = next(
        item for item in status if item["slot"] == "tinvest_invest_read"
    )
    assert invest_read["configured"] is True
    assert invest_read["required"] is True
    assert secret not in str(invest_read)
    assert load_secret(session, "tinvest_invest_read") == {"token": secret}


def test_legacy_tinvest_sandbox_slot_name_is_not_accepted(client):
    response = client.put(
        "/api/v1/integrations/tinvest_sandbox",
        json={"values": {"token": "legacy-alias-must-not-be-created"}},
    )
    assert response.status_code == 404


def test_bybit_requires_key_and_secret_together(client):
    response = client.put(
        "/api/v1/integrations/bybit_trade",
        json={"values": {"api_key": "key-only"}},
    )
    assert response.status_code == 422
    assert "api_key" in response.json()["detail"]
    assert "api_secret" in response.json()["detail"]


def test_unknown_integration_slot_is_not_created(client):
    response = client.put(
        "/api/v1/integrations/unknown",
        json={"values": {"token": "abc"}},
    )
    assert response.status_code == 404


def test_integration_can_be_removed_without_returning_secret(client):
    client.put(
        "/api/v1/integrations/bybit_read",
        json={"values": {"api_key": "key-123", "api_secret": "secret-456"}},
    )
    response = client.delete("/api/v1/integrations/bybit_read")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    status = client.get("/api/v1/integrations").json()
    item = next(row for row in status if row["slot"] == "bybit_read")
    assert item["configured"] is False
