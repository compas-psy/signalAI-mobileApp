"""P0 runtime diagnostics: request correlation and aggregate-only owner health."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import NotificationOutbox, PaperTrade, TradeIdea
from tests.conftest import DEVICE_HEADERS, idea_kwargs


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as value:
        yield value
    app.dependency_overrides.clear()


def test_health_gets_generated_request_id():
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    request_id = response.headers["X-Request-ID"]
    assert str(UUID(request_id)) == request_id


def test_valid_incoming_request_id_is_preserved():
    request_id = str(uuid4())
    with TestClient(app) as client:
        response = client.get("/health", headers={"X-Request-ID": request_id})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id


def test_invalid_incoming_request_id_is_replaced():
    with TestClient(app) as client:
        response = client.get("/health", headers={"X-Request-ID": "not-a-uuid"})

    generated = response.headers["X-Request-ID"]
    assert generated != "not-a-uuid"
    assert str(UUID(generated)) == generated


def test_unauthorized_diagnostics_response_still_has_request_id():
    with TestClient(app) as client:
        response = client.get("/api/v1/diagnostics/runtime")

    assert response.status_code == 401
    assert str(UUID(response.headers["X-Request-ID"])) == response.headers["X-Request-ID"]


def test_runtime_diagnostics_empty_state_is_aggregate_only(client):
    response = client.get("/api/v1/diagnostics/runtime")
    assert response.status_code == 200
    body = response.json()

    assert str(UUID(body["request_id"])) == body["request_id"]
    assert body["request_id"] == response.headers["X-Request-ID"]
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
        "authorization",
        "bearer",
        "access_token",
        "api_key",
        "password",
    ):
        assert forbidden not in serialized


def test_runtime_diagnostics_counts_existing_pipeline_state(client, session, instrument, now):
    watch = TradeIdea(**idea_kwargs(instrument.instrument_id, now, status="WATCH"))
    closed = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now + timedelta(minutes=5),
            status="CLOSED",
        )
    )
    session.add_all([watch, closed])
    session.flush()

    session.add(
        PaperTrade(
            idea_id=watch.id,
            instrument_id=instrument.instrument_id,
            direction="LONG",
            status="OPEN",
            entry=Decimal("90100"),
            initial_stop=Decimal("89400"),
            tp_prices=["91000", "92000"],
            tp_shares=["0.5", "0.5"],
            current_stop=Decimal("90100"),
            tps_taken=1,
            realized_r=Decimal("0.4"),
            opened_at=now,
            expires_at=now + timedelta(days=5),
            last_reconciled_at=None,
        )
    )
    session.add(
        NotificationOutbox(
            dedup_key="runtime-diagnostics-test",
            kind="IDEA",
            title="must not leak",
            body="must not leak",
            payload='{"secret":"must not leak"}',
        )
    )
    session.flush()

    response = client.get("/api/v1/diagnostics/runtime")
    assert response.status_code == 200
    body = response.json()

    assert body["ideas"]["total"] == 2
    assert body["ideas"]["by_status"] == {"CLOSED": 1, "WATCH": 1}
    assert body["ideas"]["latest_signal_at"] is not None
    assert body["paper"] == {
        "total": 1,
        "by_status": {"OPEN": 1},
        "live": 1,
        "unreconciled_live": 1,
        "oldest_live_reconciled_at": None,
    }
    assert body["notifications"]["total"] == 1
    assert body["notifications"]["latest_id"] is not None
    assert body["notifications"]["latest_created_at"] is not None
    assert "must not leak" not in response.text
