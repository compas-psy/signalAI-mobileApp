"""Decision replay diagnostics use the request correlation id without secrets."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_db
from app.main import app
from app.models import AuditEvent, TradeIdea
from app.models.enums import IdeaStatus, QualityStatus, SkipReason
from tests.conftest import DEVICE_HEADERS, idea_kwargs


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as value:
        yield value
    app.dependency_overrides.clear()


def _actionable_idea(session, instrument) -> TradeIdea:
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            datetime.now(UTC),
            status=IdeaStatus.TRIGGERED,
            quality_status=QualityStatus.ACTIVE,
            was_presented=True,
        )
    )
    session.add(idea)
    session.flush()
    return idea


def test_approve_replay_records_only_safe_correlation(client, session, instrument):
    idea = _actionable_idea(session, instrument)
    assert client.post(f"/api/v1/ideas/{idea.id}/approve-paper").status_code == 200

    request_id = str(uuid4())
    replay = client.post(
        f"/api/v1/ideas/{idea.id}/approve-paper",
        headers={"X-Request-ID": request_id},
    )

    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    event = session.execute(
        select(AuditEvent).where(AuditEvent.action == "approve_paper_replay")
    ).scalar_one()
    assert event.actor == "owner"
    assert event.subject == str(idea.id)
    assert event.trace_id == request_id
    assert event.detail == ""
    assert event.before_json == {}
    assert event.after_json == {}


def test_reject_replay_records_only_safe_correlation(client, session, instrument):
    idea = _actionable_idea(session, instrument)
    payload = {"reason": SkipReason.NO_TRUST.value, "comment": "first decision"}
    assert client.post(f"/api/v1/ideas/{idea.id}/reject", json=payload).status_code == 200

    request_id = str(uuid4())
    replay = client.post(
        f"/api/v1/ideas/{idea.id}/reject",
        headers={"X-Request-ID": request_id},
        json={"reason": SkipReason.OTHER.value, "comment": "must not overwrite"},
    )

    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    event = session.execute(
        select(AuditEvent).where(AuditEvent.action == "reject_replay")
    ).scalar_one()
    assert event.actor == "owner"
    assert event.subject == str(idea.id)
    assert event.trace_id == request_id
    assert event.detail == ""
    assert event.before_json == {}
    assert event.after_json == {}
