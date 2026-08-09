from __future__ import annotations

from datetime import UTC, datetime

from app.models import TradeIdea
from app.notification_outbox import list_after, materialize
from tests.conftest import idea_kwargs

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def test_server_materializes_smoke_and_actionable_idea_once(session, instrument):
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            NOW,
            status="TRIGGERED",
            quality_status="ACTIVE",
            was_presented=True,
        )
    )
    session.add(idea)
    session.flush()

    first_created = materialize(session, now=NOW)
    first = list_after(session, 0)
    second_created = materialize(session, now=NOW)
    second = list_after(session, 0)

    assert first_created >= 2
    assert any(event.key == "system:server-push-ready:v1" for event in first)
    assert any(event.key == f"idea:{idea.id}:actionable" for event in first)
    assert second_created == 0
    assert [event.id for event in second] == [event.id for event in first]


def test_outbox_cursor_returns_only_newer_rows(session, instrument):
    materialize(session, now=NOW)
    initial = list_after(session, 0)
    assert initial
    cursor = initial[-1].id

    # Stable materialization creates no row after the cursor.
    materialize(session, now=NOW)
    assert list_after(session, cursor) == []
