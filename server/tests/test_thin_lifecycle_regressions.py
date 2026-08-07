"""Регрессии из device acceptance: один объект — один этап lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import Bar, TradeIdea
from app.models.enums import IdeaStatus, QualityStatus, SkipReason, Timeframe
from tests.conftest import DEVICE_HEADERS, idea_kwargs


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as c:
        yield c
    app.dependency_overrides.clear()


def actionable(session, instrument, *, moment: datetime | None = None) -> TradeIdea:
    now = moment or datetime.now(UTC)
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now - timedelta(hours=2),
            status=IdeaStatus.TRIGGERED,
            quality_status=QualityStatus.ACTIVE,
            was_presented=True,
            expires_at=now + timedelta(days=2),
        )
    )
    session.add(idea)
    session.flush()
    return idea


def test_approved_idea_disappears_from_today_and_lives_only_as_paper_trade(
    client, session, instrument
):
    idea = actionable(session, instrument)
    response = client.post(f"/api/v1/ideas/{idea.id}/approve-paper")
    assert response.status_code == 200
    assert response.json()["idea_status"] == "ACTIVE"

    daily = client.get("/api/v1/ideas/today").json()
    ids = {
        row["id"]
        for row in daily["trade_now"] + daily["wait_for_trigger"]
    }
    assert str(idea.id) not in ids

    trades = client.get("/api/v1/paper/trades?live_only=false").json()
    assert len(trades) == 1
    assert trades[0]["idea_id"] == str(idea.id)


def test_approve_rechecks_market_and_rejects_plan_that_already_reached_tp3(
    client, session, instrument
):
    now = datetime.now(UTC)
    idea = actionable(session, instrument, moment=now)
    # LONG default plan: bar after signal passes the far target without a
    # need to wait for the next scheduler tick. Approve must run supervisor
    # first and close the idea as MISSED instead of opening a stale trade.
    session.add(
        Bar(
            instrument_id=instrument.instrument_id,
            timeframe=Timeframe.H1,
            open_time=now - timedelta(hours=1),
            open=Decimal("93100"),
            high=Decimal("93500"),
            low=Decimal("93050"),
            close=Decimal("93400"),
            volume_units=Decimal("1000"),
            is_closed=True,
            source="test",
        )
    )
    session.flush()

    response = client.post(f"/api/v1/ideas/{idea.id}/approve-paper")
    assert response.status_code == 409
    session.refresh(idea)
    assert idea.status is IdeaStatus.MISSED
    assert client.get("/api/v1/paper/trades?live_only=false").json() == []


def test_rejected_idea_is_visible_in_server_journal(client, session, instrument):
    idea = actionable(session, instrument)
    response = client.post(
        f"/api/v1/ideas/{idea.id}/reject",
        json={"reason": SkipReason.LATE_ENTRY.value, "comment": "проверка журнала"},
    )
    assert response.status_code == 200

    rows = client.get("/api/v1/journal/skips").json()
    row = next(item for item in rows if item["idea_id"] == str(idea.id))
    assert row["reason"] == SkipReason.LATE_ENTRY.value
    assert row["comment"] == "проверка журнала"
    assert row["symbol"] == instrument.instrument_id.split(":")[-1]
