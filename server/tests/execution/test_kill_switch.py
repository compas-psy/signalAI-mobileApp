from __future__ import annotations

import importlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.execution import enums as execution_enums
from app.execution.enums import ExecutionState
from app.execution.intent_service import (
    ExecutionIntentGate,
    ExecutionIntentRequest,
    create_execution_intent,
)
from app.execution.service import (
    ExecutionFillSnapshot,
    ExecutionPort,
    ExecutionProtectionAck,
    ExecutionSubmitAck,
    PreSubmitReconciliation,
    SubmissionReconciliation,
)
from app.execution.worker import process_next_intent
from app.models import AuditEvent, ExecutionIntent, ExecutionOrder
from app.models.enums import ExecutionMode
from app.models.ideas import TradeIdea
from app.models.risk import RiskSnapshot, RiskState
from tests.conftest import idea_kwargs


NOW = datetime(2026, 8, 19, 4, 15, tzinfo=UTC)


def _seed_intent(session: Session, instrument) -> ExecutionIntent:
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            NOW,
            status="TRIGGERED",
            quality_status="PASS",
            score=Decimal("82"),
        )
    )
    risk = RiskSnapshot(risk_equity=Decimal("100000"))
    session.add_all([idea, risk])
    session.flush()
    created = create_execution_intent(
        session,
        request=ExecutionIntentRequest(
            idea_id=idea.id,
            instrument_id=idea.instrument_id,
            strategy_version=idea.strategy_version,
            risk_policy_snapshot_id=risk.id,
            risk_override_id=None,
            venue="MOEX",
            account="sandbox-main",
            planned_quantity=Decimal("1"),
            planned_entry_price=Decimal("90100"),
            planned_stop_price=Decimal("89400"),
        ),
        gate=ExecutionIntentGate(
            owner_approved=True,
            risk_snapshot_verified=True,
            mode_allows_intent=True,
            kill_switch_clear=True,
            venue_capability_verified=True,
        ),
    )
    return created.intent


class _RecordingPort(ExecutionPort):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def reconcile_before_submit(self, intent: ExecutionIntent) -> PreSubmitReconciliation:
        self.calls.append("reconcile_before_submit")
        return PreSubmitReconciliation.absent()

    def reconcile_submission(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
    ) -> SubmissionReconciliation:
        self.calls.append("reconcile_submission")
        return SubmissionReconciliation.found(
            provider_order_id="provider-entry-1",
            status="ACKNOWLEDGED",
            acknowledged_at=NOW,
        )

    def submit(self, intent: ExecutionIntent, *, client_order_id: str) -> ExecutionSubmitAck:
        self.calls.append("submit")
        return ExecutionSubmitAck(
            provider_order_id="provider-entry-1",
            status="ACKNOWLEDGED",
            acknowledged_at=NOW,
        )

    def consume_fills(self, intent: ExecutionIntent, order: ExecutionOrder):
        self.calls.append("consume_fills")
        return [
            ExecutionFillSnapshot(
                provider_fill_id="fill-1",
                quantity=Decimal("1"),
                price=Decimal("90110"),
                fee_amount=Decimal("2.50"),
                fee_currency="RUB",
                filled_at=NOW,
            )
        ]

    def arm_protection(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
        *,
        filled_quantity: Decimal,
    ) -> ExecutionProtectionAck:
        self.calls.append("arm_protection")
        return ExecutionProtectionAck(
            provider_order_id="provider-stop-1",
            status="ACTIVE",
            armed_at=NOW,
        )

    def reconcile(self, intent: ExecutionIntent) -> None:
        self.calls.append("reconcile")

    def manage_until_close(self, intent: ExecutionIntent) -> None:
        self.calls.append("manage_until_close")


def _kill_module():
    return importlib.import_module("app.execution.kill_switch")


def _level(name: str):
    level_type = getattr(execution_enums, "ExecutionKillSwitchLevel", None)
    assert level_type is not None, "SAI-028 kill switch enum is missing"
    return level_type(name)


def test_existing_boolean_kill_switch_blocks_a_new_worker_submit(session, instrument):
    intent = _seed_intent(session, instrument)
    session.add(
        RiskState(
            id=1,
            execution_mode=ExecutionMode.PAPER,
            kill_switch=True,
            kill_switch_reason="owner halt",
        )
    )
    session.commit()
    port = _RecordingPort()

    outcome = process_next_intent(session, port=port)

    session.refresh(intent)
    assert outcome.processed is False
    assert "HALT_NEW_ENTRIES" in (outcome.blocked_reason or "")
    assert port.calls == []
    assert intent.state == ExecutionState.INTENT_CREATED


def test_kill_switch_has_three_distinct_active_levels():
    level_type = getattr(execution_enums, "ExecutionKillSwitchLevel", None)

    assert level_type is not None
    assert {item.value for item in level_type if item.value != "CLEAR"} == {
        "HALT_NEW_ENTRIES",
        "CANCEL_PENDING_ENTRIES",
        "FLATTEN_ALL",
    }


def test_cancel_pending_entries_cancels_local_intent_without_venue_io(session, instrument):
    intent = _seed_intent(session, instrument)
    kill = _kill_module()
    kill.set_execution_kill_switch(
        session,
        level=_level("CANCEL_PENDING_ENTRIES"),
        actor="owner",
        reason="cancel pending entries",
    )
    session.commit()
    port = _RecordingPort()

    outcome = process_next_intent(session, port=port)

    session.refresh(intent)
    assert outcome.processed is True
    assert port.calls == []
    assert intent.state == ExecutionState.CANCELLED


def test_halt_keeps_ambiguous_reconciliation_and_protection_alive(session, instrument):
    intent = _seed_intent(session, instrument)
    intent.state = ExecutionState.AMBIGUOUS
    order = ExecutionOrder(
        intent_id=intent.id,
        client_order_id=f"e-{intent.id.hex}",
        provider_order_id=None,
        side="BUY",
        order_type="ENTRY",
        status="SUBMITTING",
        quantity=Decimal("1"),
        limit_price=Decimal("90100"),
        stop_price=None,
        submitted_at=NOW,
        acknowledged_at=None,
    )
    session.add(order)
    kill = _kill_module()
    kill.set_execution_kill_switch(
        session,
        level=_level("HALT_NEW_ENTRIES"),
        actor="system",
        reason="resource pressure",
    )
    session.commit()
    port = _RecordingPort()

    outcome = process_next_intent(session, port=port)

    assert outcome.processed is True
    assert port.calls == [
        "reconcile_submission",
        "consume_fills",
        "arm_protection",
        "reconcile",
        "manage_until_close",
    ]


def test_flatten_all_requires_explicit_confirmation_and_audits_exact_level(
    session,
):
    kill = _kill_module()

    with pytest.raises(kill.ExecutionKillSwitchError, match="confirm"):
        kill.set_execution_kill_switch(
            session,
            level=_level("FLATTEN_ALL"),
            actor="owner",
            reason="manual emergency",
        )

    state = kill.set_execution_kill_switch(
        session,
        level=_level("FLATTEN_ALL"),
        actor="owner",
        reason="manual emergency",
        confirm_flatten_all=True,
    )
    session.flush()

    assert state.kill_switch is True
    assert state.kill_switch_level == _level("FLATTEN_ALL")
    event = session.query(AuditEvent).order_by(AuditEvent.occurred_at.desc()).first()
    assert event is not None
    assert event.action == "execution_kill_switch_set"
    assert event.after_json["level"] == "FLATTEN_ALL"


def test_clear_resets_level_and_legacy_boolean(session):
    kill = _kill_module()
    kill.set_execution_kill_switch(
        session,
        level=_level("HALT_NEW_ENTRIES"),
        actor="owner",
        reason="manual halt",
    )

    state = kill.clear_execution_kill_switch(
        session,
        actor="owner",
        reason="conditions normalized",
    )

    assert state.kill_switch_level == _level("CLEAR")
    assert state.kill_switch is False
    assert state.kill_switch_reason == ""


def test_risk_api_accepts_and_returns_exact_kill_switch_level(session):
    risk_api = importlib.import_module("app.api.v1.risk")
    request_type = getattr(risk_api, "KillSwitchRequest", None)
    endpoint = getattr(risk_api, "set_kill_switch", None)

    assert request_type is not None
    assert endpoint is not None
    request = request_type(
        level=_level("CANCEL_PENDING_ENTRIES"),
        reason="owner cancel request",
        confirm_flatten_all=False,
    )

    dashboard = endpoint(request=request, db=session)

    assert dashboard.kill_switch is True
    assert dashboard.kill_switch_level == _level("CANCEL_PENDING_ENTRIES")
