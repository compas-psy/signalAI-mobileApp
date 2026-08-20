from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_db
from app.execution.manual_controls import ManualTradeAction, request_manual_trade_control
from app.main import app
from app.models import (
    ExecutionOrder,
    ExecutionProtection,
    ExecutionReconciliationEvent,
)
from tests.conftest import DEVICE_HEADERS
from tests.execution.test_manual_trade_controls_contract import _managed_intent


FACT_TIME = datetime(2026, 8, 20, 14, 20, tzinfo=UTC)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_timeline_facts(session, instrument):
    intent, snapshot = _managed_intent(session, instrument)
    entry = session.execute(
        select(ExecutionOrder)
        .where(ExecutionOrder.intent_id == intent.id)
        .order_by(ExecutionOrder.created_at, ExecutionOrder.id)
    ).scalars().first()
    assert entry is not None

    protection = ExecutionProtection(
        intent_id=intent.id,
        order_id=entry.id,
        protection_type="STOP_LOSS",
        status="ARMED",
        provider_order_id="protect-provider-1",
        quantity=Decimal("4"),
        stop_price=Decimal("89500"),
        armed_at=FACT_TIME + timedelta(seconds=10),
        last_reconciled_at=FACT_TIME + timedelta(seconds=20),
    )
    reconciliation = ExecutionReconciliationEvent(
        intent_id=intent.id,
        event_type="POSITION_RECONCILED",
        outcome="MATCHED",
        detail_json={"position_quantity": "4", "source": "provider-stream"},
        occurred_at=FACT_TIME + timedelta(seconds=30),
    )
    session.add_all([protection, reconciliation])
    session.flush()

    manual = request_manual_trade_control(
        session,
        intent_id=intent.id,
        action=ManualTradeAction.REDUCE,
        idempotency_key="timeline-reduce-secret",
        owner_reason="Сократить после проверки",
        requested_quantity=Decimal("1.250000000000"),
        requested_stop=None,
    )
    session.commit()
    return intent, snapshot, manual.command


def test_execution_timeline_projects_only_durable_facts_in_stable_time_order(
    client, session, instrument
):
    intent, snapshot, command = _seed_timeline_facts(session, instrument)

    response = client.get(f"/api/v1/execution/ideas/{intent.idea_id}/timeline")

    assert response.status_code == 200
    body = response.json()
    assert body["idea_id"] == str(intent.idea_id)
    assert str(intent.id) in body["intent_ids"]
    events = body["events"]
    assert events

    occurred = [datetime.fromisoformat(event["occurred_at"]) for event in events]
    assert occurred == sorted(occurred)

    sources = {event["source"] for event in events}
    assert {
        "intent",
        "order",
        "fill",
        "protection",
        "reconciliation",
        "management_policy",
        "manual_control",
    }.issubset(sources)

    manual_event = next(
        event
        for event in events
        if event["source"] == "manual_control"
        and event["facts"].get("command_id") == str(command.id)
    )
    assert manual_event["kind"] == "MANUAL_REDUCE_REQUESTED"
    assert manual_event["facts"]["quantity"] == "1.250000000000"
    assert manual_event["facts"]["management_policy_snapshot_id"] == str(snapshot.id)
    assert manual_event["facts"]["reduce_only"] is True
    serialized = str(body)
    assert "timeline-reduce-secret" not in serialized
    assert "idempotency_key_sha256" not in serialized


def test_execution_timeline_does_not_invent_acknowledgements_or_provider_execution(
    client, session, instrument
):
    intent, _snapshot = _managed_intent(session, instrument)
    pending = ExecutionOrder(
        intent_id=intent.id,
        client_order_id=f"pending-{intent.id.hex}",
        provider_order_id=None,
        side="SELL",
        order_type="MANUAL_CLOSE",
        status="REQUESTED",
        quantity=Decimal("4"),
        limit_price=None,
        stop_price=None,
        submitted_at=None,
        acknowledged_at=None,
    )
    session.add(pending)
    session.commit()

    response = client.get(f"/api/v1/execution/ideas/{intent.idea_id}/timeline")

    assert response.status_code == 200
    events = response.json()["events"]
    pending_events = [
        event
        for event in events
        if event["source"] == "order"
        and event["facts"].get("order_id") == str(pending.id)
    ]
    assert [event["kind"] for event in pending_events] == ["ORDER_CREATED"]
    assert all("ACKNOWLEDGED" not in event["kind"] for event in pending_events)
    assert all("EXECUTED" not in event["kind"] for event in pending_events)


def test_execution_timeline_remains_available_after_execution_is_closed(
    client, session, instrument
):
    intent, _snapshot = _managed_intent(session, instrument)
    from app.execution.enums import ExecutionState

    intent.state = ExecutionState.CLOSED
    session.commit()

    response = client.get(f"/api/v1/execution/ideas/{intent.idea_id}/timeline")

    assert response.status_code == 200
    body = response.json()
    assert body["intent_ids"] == [str(intent.id)]
    assert any(event["source"] == "intent" for event in body["events"])


def test_execution_timeline_returns_404_for_idea_without_execution(client):
    from uuid import uuid4

    response = client.get(f"/api/v1/execution/ideas/{uuid4()}/timeline")

    assert response.status_code == 404
