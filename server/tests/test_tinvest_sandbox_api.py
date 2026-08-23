from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.execution.venues.tinvest_sandbox_smoke import TInvestSandboxSmokeResult
from app.main import app
from tests.conftest import DEVICE_HEADERS


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()


def test_sandbox_smoke_requires_active_device_auth(client):
    response = client.post(
        "/api/v1/tinvest-sandbox/smoke",
        headers={"X-Idempotency-Key": "owner-smoke-2026-08-23-api01"},
    )
    assert response.status_code == 401


def test_sandbox_smoke_returns_only_sanitized_fill_evidence(client, monkeypatch):
    import app.api.v1.tinvest_sandbox as route

    captured = {}

    def fake_run(db, *, diagnostic_key: str):
        captured["key"] = diagnostic_key
        return TInvestSandboxSmokeResult(
            filled=True,
            symbol="SBER",
            account_suffix="123456",
            provider_order_id="exchange-order-safe",
            execution_status="EXECUTION_REPORT_STATUS_FILL",
            executed_lots=1,
        )

    monkeypatch.setattr(route, "run_tinvest_sandbox_smoke", fake_run)
    response = client.post(
        "/api/v1/tinvest-sandbox/smoke",
        headers={
            **DEVICE_HEADERS,
            "X-Idempotency-Key": "owner-smoke-2026-08-23-api02",
        },
    )
    assert response.status_code == 200
    assert captured["key"] == "owner-smoke-2026-08-23-api02"
    assert response.json() == {
        "filled": True,
        "symbol": "SBER",
        "account_suffix": "123456",
        "provider_order_id": "exchange-order-safe",
        "execution_status": "EXECUTION_REPORT_STATUS_FILL",
        "executed_lots": 1,
    }
    public = response.text.lower()
    assert "token" not in public
    assert "authorization" not in public
    assert "private" not in public


def test_sandbox_smoke_rejects_missing_or_malformed_idempotency_key(client):
    missing = client.post(
        "/api/v1/tinvest-sandbox/smoke",
        headers=DEVICE_HEADERS,
    )
    assert missing.status_code == 400

    malformed = client.post(
        "/api/v1/tinvest-sandbox/smoke",
        headers={**DEVICE_HEADERS, "X-Idempotency-Key": "../bad"},
    )
    assert malformed.status_code == 400
