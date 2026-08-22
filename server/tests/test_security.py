"""Проверки внешнего доступа к бизнес-API."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app as signalai_app
from app.security import DeviceTokenMiddleware


class _Device:
    id = "fixture-device"
    device_id = "fixture-device"
    generation = 1


def _client(*, active_token: str = "issued-device-token") -> TestClient:
    app = FastAPI()
    app.add_middleware(
        DeviceTokenMiddleware,
        authenticate=lambda token: _Device() if token == active_token else None,
    )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/v1/private")
    def private():
        return {"ok": True}

    return TestClient(app)


def test_health_is_public(monkeypatch):
    monkeypatch.delenv("SIGNALAI_DEVICE_TOKEN", raising=False)
    response = _client().get("/health")
    assert response.status_code == 200


def test_external_api_fails_closed_without_active_token(monkeypatch):
    monkeypatch.delenv("SIGNALAI_DEVICE_TOKEN", raising=False)
    response = _client().get("/api/v1/private")
    assert response.status_code == 401
    assert "не авторизовано" in response.json()["error"]["message"]


def test_external_api_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("SIGNALAI_DEVICE_TOKEN", "correct-token")
    response = _client().get(
        "/api/v1/private",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_external_api_accepts_matching_token(monkeypatch):
    monkeypatch.setenv("SIGNALAI_DEVICE_TOKEN", "correct-token")
    response = _client().get(
        "/api/v1/private",
        headers={"Authorization": "Bearer issued-device-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_bootstrap_token_is_never_a_business_api_token(monkeypatch):
    monkeypatch.setenv("SIGNALAI_DEVICE_TOKEN", "bootstrap-only-token")
    response = _client().get(
        "/api/v1/private",
        headers={"Authorization": "Bearer bootstrap-only-token"},
    )
    assert response.status_code == 401


def test_unknown_device_auth_policy_fails_closed(monkeypatch):
    monkeypatch.setenv("SIGNALAI_DEVICE_AUTH_POLICY", "legacy-bootstrap")
    response = _client().get(
        "/api/v1/private",
        headers={"Authorization": "Bearer issued-device-token"},
    )
    assert response.status_code == 503


def test_real_approve_route_requires_bearer(monkeypatch):
    """Middleware protects the actual paper-decision endpoint, not a mock only."""
    # The globally configured app owns a database-backed middleware, so this
    # unit test must not turn a malformed UUID check into an accidental DB
    # dependency.  The local middleware test above proves the same boundary.
    monkeypatch.setenv("SIGNALAI_DEVICE_TOKEN", "bootstrap-only-token")
    with TestClient(signalai_app) as client:
        denied = client.post("/api/v1/ideas/not-a-uuid/approve-paper")

    assert denied.status_code == 401
