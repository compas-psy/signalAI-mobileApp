from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from tests.conftest import DEVICE_HEADERS


_STEP_UP_BLOCKER = "LIVE_TRADE_CREDENTIAL_STEP_UP_REQUIRED"


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("slot", "values"),
    [
        ("tinvest_trade", {"token": "t.live-trade-secret"}),
        ("bybit_trade", {"api_key": "live-key", "api_secret": "live-secret"}),
    ],
)
def test_device_bearer_cannot_provision_or_rotate_live_trade_credentials(
    client,
    slot,
    values,
):
    response = client.put(f"/api/v1/integrations/{slot}", json={"values": values})

    assert response.status_code == 409
    assert response.json()["detail"] == _STEP_UP_BLOCKER


@pytest.mark.parametrize("slot", ["tinvest_trade", "bybit_trade"])
def test_device_bearer_cannot_revoke_live_trade_credentials(client, slot):
    response = client.delete(f"/api/v1/integrations/{slot}")

    assert response.status_code == 409
    assert response.json()["detail"] == _STEP_UP_BLOCKER


def test_read_and_testnet_credentials_remain_device_manageable(client):
    read = client.put(
        "/api/v1/integrations/bybit_read",
        json={"values": {"api_key": "read-key", "api_secret": "read-secret"}},
    )
    testnet = client.put(
        "/api/v1/integrations/bybit_testnet_trade",
        json={"values": {"api_key": "test-key", "api_secret": "test-secret"}},
    )

    assert read.status_code == 200
    assert testnet.status_code == 200
