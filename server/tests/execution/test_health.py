from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.execution.intent_service import (
    ExecutionIntentGate,
    ExecutionIntentRequest,
    create_execution_intent,
)
from app.models import (
    ExecutionFill,
    ExecutionOrder,
    ExecutionProtection,
    ExecutionReconciliationEvent,
)
from app.models.enums import ExecutionMode
from app.models.ideas import TradeIdea
from app.models.risk import RiskSnapshot, RiskState
from tests.conftest import idea_kwargs


BASE = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


def _request(session: Session, instrument):
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            BASE,
            status="TRIGGERED",
            quality_status="PASS",
            score=Decimal("82"),
        )
    )
    risk = RiskSnapshot(risk_equity=Decimal("100000"))
    session.add_all([idea, risk])
    session.flush()
    request = ExecutionIntentRequest(
        idea_id=idea.id,
        instrument_id=idea.instrument_id,
        strategy_version=idea.strategy_version,
        risk_policy_snapshot_id=risk.id,
        risk_override_id=None,
        venue="MOEX",
        account="sandbox-main",
        planned_quantity=Decimal("2"),
        planned_entry_price=Decimal("100"),
        planned_stop_price=Decimal("95"),
    )
    gate = ExecutionIntentGate(
        owner_approved=True,
        risk_snapshot_verified=True,
        mode_allows_intent=True,
        kill_switch_clear=True,
        venue_capability_verified=True,
    )
    return idea, request, gate


def _seed_full_execution(session: Session, instrument):
    idea, request, gate = _request(session, instrument)
    creation = create_execution_intent(session, request=request, gate=gate)
    intent = creation.intent
    intent.created_at = BASE + timedelta(milliseconds=250)
    session.flush()

    entry = ExecutionOrder(
        intent_id=intent.id,
        client_order_id=f"e-{intent.id.hex}",
        provider_order_id="entry-provider-1",
        side="BUY",
        order_type="ENTRY",
        status="ACKNOWLEDGED",
        quantity=Decimal("2"),
        limit_price=Decimal("100"),
        stop_price=None,
        submitted_at=BASE + timedelta(seconds=1),
        acknowledged_at=BASE + timedelta(seconds=1, milliseconds=120),
    )
    rejected = ExecutionOrder(
        intent_id=intent.id,
        client_order_id=f"r-{intent.id.hex}",
        provider_order_id="rejected-provider-1",
        side="BUY",
        order_type="ENTRY_RETRY",
        status="REJECTED",
        quantity=Decimal("2"),
        limit_price=Decimal("100"),
        stop_price=None,
        submitted_at=BASE + timedelta(seconds=3),
        acknowledged_at=BASE + timedelta(seconds=3, milliseconds=80),
    )
    session.add_all([entry, rejected])
    session.flush()
    session.add(
        ExecutionFill(
            intent_id=intent.id,
            order_id=entry.id,
            provider_fill_id="fill-1",
            quantity=Decimal("2"),
            price=Decimal("100.25"),
            fee_amount=Decimal("0"),
            fee_currency="RUB",
            filled_at=BASE + timedelta(seconds=2),
        )
    )
    session.add(
        ExecutionProtection(
            intent_id=intent.id,
            order_id=entry.id,
            protection_type="STOP",
            status="ACTIVE",
            provider_order_id="stop-provider-1",
            quantity=Decimal("2"),
            stop_price=Decimal("95"),
            armed_at=BASE + timedelta(seconds=32, milliseconds=500),
            last_reconciled_at=BASE + timedelta(seconds=33),
        )
    )
    session.add(
        ExecutionReconciliationEvent(
            intent_id=intent.id,
            event_type="SUBMISSION_RECOVERY",
            outcome="UNKNOWN",
            detail_json={"reason": "provider timeout"},
            occurred_at=BASE + timedelta(seconds=4),
        )
    )
    session.flush()
    return idea, request, gate, intent


def test_duplicate_prevention_is_counted_per_execution_intent(session, instrument):
    _, request, gate = _request(session, instrument)

    first = create_execution_intent(session, request=request, gate=gate)
    second = create_execution_intent(session, request=request, gate=gate)
    third = create_execution_intent(session, request=request, gate=gate)
    session.flush()
    session.refresh(first.intent)

    assert first.created is True
    assert second.created is False
    assert third.created is False
    assert first.intent.duplicate_prevention_count == 2


def test_per_trade_health_reports_all_required_metrics_and_violations(
    session,
    instrument,
):
    health = importlib.import_module("app.execution.health")
    models = importlib.import_module("app.models.execution")
    idea, _, _, intent = _seed_full_execution(session, instrument)
    session.add(
        models.ExecutionVenueHealth(
            venue="MOEX",
            account="sandbox-main",
            websocket_connected=True,
            last_websocket_message_at=BASE + timedelta(seconds=20),
            stale_after_seconds=5,
        )
    )
    intent.duplicate_prevention_count = 2
    session.flush()

    report = health.execution_health_for_intent(
        session,
        intent_id=intent.id,
        as_of=BASE + timedelta(seconds=30),
    )

    assert report.intent_id == intent.id
    assert report.idea_id == idea.id
    assert report.decision_to_intent_ms == 250
    assert report.submit_to_ack_ms == 120
    assert report.fill_deviation_bps == Decimal("25.00")
    assert report.protection_arm_ms == 30500
    assert report.protection_sla_ms == 30000
    assert report.reconciliation_mismatch_count == 1
    assert report.websocket_state == "STALE"
    assert report.websocket_stale is True
    assert report.rejected_order_count == 1
    assert report.duplicate_prevention_count == 2
    assert {item.code for item in report.violations} == {
        "PROTECTION_ARM_SLO",
        "RECONCILIATION_MISMATCH",
        "STALE_WEBSOCKET",
        "REJECTED_ORDER",
    }


def test_missing_websocket_telemetry_is_explicitly_not_configured(
    session,
    instrument,
):
    health = importlib.import_module("app.execution.health")
    _, _, _, intent = _seed_full_execution(session, instrument)

    report = health.execution_health_for_intent(
        session,
        intent_id=intent.id,
        as_of=BASE + timedelta(seconds=30),
    )

    assert report.websocket_state == "NOT_CONFIGURED"
    assert report.websocket_stale is None
    assert all(item.code != "STALE_WEBSOCKET" for item in report.violations)


def test_health_api_returns_trade_level_rows_not_only_aggregate(
    session,
    instrument,
):
    api = importlib.import_module("app.api.v1.execution_health")
    _, _, _, intent = _seed_full_execution(session, instrument)

    result = api.list_execution_health(limit=20, db=session)

    assert result.items
    row = next(item for item in result.items if item.intent_id == intent.id)
    assert row.instrument_id == instrument.instrument_id
    assert row.protection_arm_ms == 30500
    assert row.violations
    assert result.aggregate.total_intents >= 1
    assert result.aggregate.violation_intents >= 1


def test_legacy_risk_state_does_not_fake_websocket_health(session):
    health = importlib.import_module("app.execution.health")
    session.add(
        RiskState(
            id=1,
            execution_mode=ExecutionMode.PAPER,
            kill_switch=False,
            kill_switch_reason="",
        )
    )
    session.flush()

    aggregate = health.execution_health_summary(session, limit=20, as_of=BASE)

    assert aggregate.websocket_configured_intents == 0
