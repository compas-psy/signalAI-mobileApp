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


def test_sandbox_smoke_returns_only_sanitized_roundtrip_evidence(client, monkeypatch):
    import app.api.v1.tinvest_sandbox as route

    captured = {}
    context = object()

    def fake_run(db, *, diagnostic_key: str):
        captured["key"] = diagnostic_key
        return TInvestSandboxSmokeResult(
            round_trip_complete=True,
            symbol="LQDT",
            account_suffix="123456",
            buy_provider_order_id="buy-order-safe",
            buy_execution_status="EXECUTION_REPORT_STATUS_FILL",
            buy_executed_lots=1,
            sell_provider_order_id="sell-order-safe",
            sell_execution_status="EXECUTION_REPORT_STATUS_FILL",
            sell_executed_lots=1,
            position_flat=True,
        )

    monkeypatch.setattr(route, "current_tinvest_sandbox_context", lambda db: context)
    monkeypatch.setattr(
        route,
        "scoped_sandbox_diagnostic_key",
        lambda client_key, ctx: f"scoped-{client_key}",
    )
    monkeypatch.setattr(route, "run_tinvest_sandbox_smoke", fake_run)
    monkeypatch.setattr(
        route,
        "record_tinvest_sandbox_roundtrip_proof",
        lambda db, **kwargs: "proof-safe",
    )
    response = client.post(
        "/api/v1/tinvest-sandbox/smoke",
        headers={
            **DEVICE_HEADERS,
            "X-Idempotency-Key": "owner-smoke-2026-08-23-api02",
        },
    )
    assert response.status_code == 200
    assert captured["key"] == "scoped-owner-smoke-2026-08-23-api02"
    assert response.json() == {
        "round_trip_complete": True,
        "symbol": "LQDT",
        "account_suffix": "123456",
        "buy_provider_order_id": "buy-order-safe",
        "buy_execution_status": "EXECUTION_REPORT_STATUS_FILL",
        "buy_executed_lots": 1,
        "sell_provider_order_id": "sell-order-safe",
        "sell_execution_status": "EXECUTION_REPORT_STATUS_FILL",
        "sell_executed_lots": 1,
        "position_flat": True,
        "readiness_proof_id": "proof-safe",
    }
    public = response.text.lower()
    assert "token" not in public
    assert "authorization" not in public
    assert "private" not in public


def test_incomplete_roundtrip_is_not_recorded_as_readiness(client, monkeypatch):
    import app.api.v1.tinvest_sandbox as route

    recorded = []
    monkeypatch.setattr(route, "current_tinvest_sandbox_context", lambda db: object())
    monkeypatch.setattr(
        route,
        "scoped_sandbox_diagnostic_key",
        lambda client_key, ctx: f"scoped-{client_key}",
    )
    monkeypatch.setattr(
        route,
        "run_tinvest_sandbox_smoke",
        lambda db, diagnostic_key: TInvestSandboxSmokeResult(
            round_trip_complete=False,
            symbol="LQDT",
            account_suffix="123456",
            buy_provider_order_id="buy-order-safe",
            buy_execution_status="EXECUTION_REPORT_STATUS_FILL",
            buy_executed_lots=1,
            sell_provider_order_id="sell-order-safe",
            sell_execution_status="EXECUTION_REPORT_STATUS_NEW",
            sell_executed_lots=0,
            position_flat=False,
        ),
    )
    monkeypatch.setattr(
        route,
        "record_tinvest_sandbox_roundtrip_proof",
        lambda db, **kwargs: recorded.append(kwargs),
    )

    response = client.post(
        "/api/v1/tinvest-sandbox/smoke",
        headers={
            **DEVICE_HEADERS,
            "X-Idempotency-Key": "owner-smoke-2026-08-23-api03",
        },
    )
    assert response.status_code == 409
    assert recorded == []
    assert "round trip is incomplete" in response.json()["detail"]


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
