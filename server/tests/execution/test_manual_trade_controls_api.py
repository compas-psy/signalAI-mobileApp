from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from tests.conftest import DEVICE_HEADERS
from tests.execution.test_manual_trade_controls_contract import _managed_intent


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_manual_close_api_accepts_mobile_idempotency_header_and_returns_requested_not_executed(
    client, session, instrument
):
    intent, snapshot = _managed_intent(session, instrument)

    response = client.post(
        f"/api/v1/execution/intents/{intent.id}/control",
        headers={"X-Idempotency-Key": "mobile-close-1"},
        json={"action": "CLOSE", "reason": "Закрыть сейчас"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["intent_id"] == str(intent.id)
    assert body["management_policy_snapshot_id"] == str(snapshot.id)
    assert body["action"] == "CLOSE"
    assert body["status"] == "REQUESTED"
    assert body["reduce_only"] is True
    assert body["quantity"] == "4.000000000000"
    assert body["stop_price"] is None
    assert body["order_status"] == "REQUESTED"
    assert body["created"] is True
    assert "provider_order_id" not in body


def test_manual_control_api_is_replay_safe(client, session, instrument):
    intent, _snapshot = _managed_intent(session, instrument)
    request = {
        "action": "TIGHTEN_STOP",
        "stop_price": "89700",
        "reason": "Подтянуть стоп",
    }

    first = client.post(
        f"/api/v1/execution/intents/{intent.id}/control",
        headers={"Idempotency-Key": "tight-api-1"},
        json=request,
    )
    replay = client.post(
        f"/api/v1/execution/intents/{intent.id}/control",
        headers={"Idempotency-Key": "tight-api-1"},
        json=request,
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json()["created"] is True
    assert replay.json()["created"] is False
    assert first.json()["command_id"] == replay.json()["command_id"]
    assert first.json()["order_id"] == replay.json()["order_id"]


def test_manual_control_api_rejects_risk_widening_and_client_safety_overrides(
    client, session, instrument
):
    intent, _snapshot = _managed_intent(session, instrument)

    widened = client.post(
        f"/api/v1/execution/intents/{intent.id}/control",
        headers={"Idempotency-Key": "widen-api-1"},
        json={
            "action": "TIGHTEN_STOP",
            "stop_price": "89000",
            "reason": "Не должно пройти",
        },
    )
    assert widened.status_code == 409
    assert "lower risk" in widened.json()["detail"]

    injected = client.post(
        f"/api/v1/execution/intents/{intent.id}/control",
        headers={"Idempotency-Key": "inject-api-1"},
        json={
            "action": "REDUCE",
            "quantity": "1",
            "reason": "reduce",
            "reduce_only": False,
        },
    )
    assert injected.status_code == 422


def test_manual_control_api_requires_idempotency_key(client, session, instrument):
    intent, _snapshot = _managed_intent(session, instrument)

    response = client.post(
        f"/api/v1/execution/intents/{intent.id}/control",
        json={"action": "REDUCE", "quantity": "1", "reason": "reduce"},
    )

    assert response.status_code == 422


def test_manual_reduce_api_cannot_equal_current_exposure(client, session, instrument):
    intent, _snapshot = _managed_intent(session, instrument)

    response = client.post(
        f"/api/v1/execution/intents/{intent.id}/control",
        headers={"Idempotency-Key": "reduce-all-api"},
        json={
            "action": "REDUCE",
            "quantity": str(Decimal("4")),
            "reason": "must use CLOSE",
        },
    )

    assert response.status_code == 409
    assert "strictly below" in response.json()["detail"]
