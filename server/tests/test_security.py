"""Проверки внешнего доступа к бизнес-API."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security import DeviceTokenMiddleware


def _client() -> TestClient:
    app = FastAPI()
    app.add_middleware(DeviceTokenMiddleware)

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


def test_external_api_fails_closed_without_server_token(monkeypatch):
    monkeypatch.delenv("SIGNALAI_DEVICE_TOKEN", raising=False)
    response = _client().get("/api/v1/private")
    assert response.status_code == 503
    assert "внешний API закрыт" in response.json()["error"]["message"]


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
        headers={"Authorization": "Bearer correct-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
