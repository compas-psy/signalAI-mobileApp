"""P0 runtime diagnostics: request correlation and aggregate-only owner health."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import (
    AuditEvent,
    DataQualityEvent,
    IdeaEvent,
    IdeaSkip,
    NotificationOutbox,
    PaperTrade,
    TradeIdea,
)
from app.models.enums import IdeaStatus, SkipReason
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
    assert body["decisions"] == {"approved": 0, "rejected": 0}
    assert body["lifecycle"] == {
        "total": 0,
        "by_status": {},
        "latest_event_at": None,
    }
    assert body["data_quality"] == {
        "total": 0,
        "by_flag": {},
        "latest_event_at": None,
    }
    assert body["idempotency"] == {
        "approve_replays": 0,
        "reject_replays": 0,
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


def test_runtime_diagnostics_aggregates_lifecycle_quality_decisions_and_replays(
    client, session, instrument, now
):
    watch = TradeIdea(**idea_kwargs(instrument.instrument_id, now, status="WATCH"))
    rejected = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now + timedelta(minutes=5),
            status=IdeaStatus.CANCELLED,
        )
    )
    session.add_all([watch, rejected])
    session.flush()

    session.add(
        PaperTrade(
            idea_id=watch.id,
            instrument_id=instrument.instrument_id,
            direction="LONG",
            status="CLOSED",
            entry=Decimal("90100"),
            initial_stop=Decimal("89400"),
            tp_prices=["91000", "92000"],
            tp_shares=["0.5", "0.5"],
            current_stop=Decimal("90100"),
            tps_taken=0,
            realized_r=Decimal("0"),
            opened_at=now,
            expires_at=now + timedelta(days=5),
            closed_at=now + timedelta(hours=1),
        )
    )
    session.add(
        IdeaSkip(
            idea_id=rejected.id,
            reason=SkipReason.OTHER,
            comment="must not leak rejection comment",
            snapshot_json={"secret": "must not leak rejection snapshot"},
        )
    )
    session.add_all(
        [
            IdeaEvent(
                idea_id=watch.id,
                sequence=1,
                old_status=IdeaStatus.DISCOVERED,
                new_status=IdeaStatus.WATCH,
                reason_code="candidate",
                reason_detail="must not leak lifecycle detail",
                market_snapshot={"secret": "must not leak lifecycle snapshot"},
                feature_snapshot={},
                config_hash="0" * 64,
                engine_version="0.1.0",
            ),
            IdeaEvent(
                idea_id=rejected.id,
                sequence=1,
                old_status=IdeaStatus.WATCH,
                new_status=IdeaStatus.CANCELLED,
                reason_code="user_rejected",
                reason_detail="must not leak lifecycle detail",
                market_snapshot={},
                feature_snapshot={},
                config_hash="0" * 64,
                engine_version="0.1.0",
                user_action=True,
            ),
        ]
    )
    session.add_all(
        [
            DataQualityEvent(
                source="moex",
                flag="STALE_CANDLES",
                detail="must not leak data quality detail",
                payload_json={"secret": "must not leak dq payload"},
            ),
            DataQualityEvent(
                source="moex",
                flag="MISSING_CANDLES",
                detail="must not leak data quality detail",
                payload_json={},
            ),
            DataQualityEvent(
                source="t-invest",
                flag="STALE_CANDLES",
                detail="must not leak data quality detail",
                payload_json={},
            ),
        ]
    )
    session.add_all(
        [
            AuditEvent(
                actor="owner",
                action="approve_paper_replay",
                subject=str(watch.id),
                trace_id=str(uuid4()),
            ),
            AuditEvent(
                actor="owner",
                action="approve_paper_replay",
                subject=str(watch.id),
                trace_id=str(uuid4()),
            ),
            AuditEvent(
                actor="owner",
                action="reject_replay",
                subject=str(rejected.id),
                trace_id=str(uuid4()),
            ),
        ]
    )
    session.flush()

    response = client.get("/api/v1/diagnostics/runtime")
    assert response.status_code == 200
    body = response.json()

    assert body["decisions"] == {"approved": 1, "rejected": 1}
    assert body["lifecycle"]["total"] == 2
    assert body["lifecycle"]["by_status"] == {"CANCELLED": 1, "WATCH": 1}
    assert body["lifecycle"]["latest_event_at"] is not None
    assert body["data_quality"]["total"] == 3
    assert body["data_quality"]["by_flag"] == {
        "MISSING_CANDLES": 1,
        "STALE_CANDLES": 2,
    }
    assert body["data_quality"]["latest_event_at"] is not None
    assert body["idempotency"] == {
        "approve_replays": 2,
        "reject_replays": 1,
    }

    serialized = response.text.lower()
    assert "must not leak" not in serialized
    assert "trace_id" not in serialized
