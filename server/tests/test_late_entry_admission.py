"""P0 regression: a stale FORTS setup must never become a new trade.

The chart already knows when price has run through the planned targets before
an owner enters. The money boundary must use the same fresh market path instead
of trusting the immutable TRIGGERED snapshot alone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_db
from app.main import app
from app.market.candles import Candle
from app.models import PaperTrade, TradeIdea
from app.models.enums import IdeaStatus, QualityStatus
from tests.calendar_support import configure_clear_event_calendar
from tests.conftest import DEVICE_HEADERS, idea_kwargs


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as c:
        yield c
    app.dependency_overrides.clear()


def _actionable_forts_idea(session, instrument, monkeypatch, tmp_path) -> TradeIdea:
    configure_clear_event_calendar(monkeypatch, tmp_path)
    monkeypatch.setenv("SIGNALAI_TEST_CLEAR_CALENDAR_FIXTURE", "1")
    signal_time = datetime.now(UTC) - timedelta(minutes=30)
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            signal_time,
            status=IdeaStatus.TRIGGERED,
            quality_status=QualityStatus.ACTIVE,
            was_presented=True,
        )
    )
    session.add(idea)
    session.flush()
    return idea


def _closed_candle(
    idea: TradeIdea,
    *,
    high: str,
    low: str,
    close: str,
) -> Candle:
    high_d = Decimal(high)
    low_d = Decimal(low)
    return Candle(
        open_time=idea.signal_time + timedelta(minutes=10),
        open=(high_d + low_d) / 2,
        high=high_d,
        low=low_d,
        close=Decimal(close),
        is_closed=True,
        source="test",
    )


def test_approve_paper_rejects_forts_when_tp2_traded_before_entry(
    client, session, instrument, monkeypatch, tmp_path
):
    """The exact contradiction from the owner screenshot is impossible."""
    idea = _actionable_forts_idea(session, instrument, monkeypatch, tmp_path)
    # LONG plan: entry is 90 000–90 200. This complete post-signal candle
    # traded TP1 and TP2 without ever trading the entry zone/reference.
    candle = _closed_candle(idea, high="92050", low="90950", close="91800")
    monkeypatch.setattr(
        "app.api.v1.idea_progress.guarded_candles",
        lambda *args, **kwargs: ([candle], None),
    )

    response = client.post(f"/api/v1/ideas/{idea.id}/approve-paper")

    assert response.status_code == 409
    detail = response.json()["detail"].lower()
    assert "позд" in detail or "упущ" in detail
    assert session.execute(select(PaperTrade)).scalars().all() == []


def test_approve_paper_keeps_valid_forts_entry_path_available(
    client, session, instrument, monkeypatch, tmp_path
):
    """The P0 guard must not turn every FORTS approval into a rejection."""
    idea = _actionable_forts_idea(session, instrument, monkeypatch, tmp_path)
    candle = _closed_candle(idea, high="90500", low="90300", close="90400")
    monkeypatch.setattr(
        "app.api.v1.idea_progress.guarded_candles",
        lambda *args, **kwargs: ([candle], None),
    )

    response = client.post(f"/api/v1/ideas/{idea.id}/approve-paper")

    assert response.status_code == 200
    assert response.json()["decision"] == "APPROVED_PAPER"
    assert len(session.execute(select(PaperTrade)).scalars().all()) == 1
