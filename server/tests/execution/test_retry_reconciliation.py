from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.execution.enums import ExecutionState
from app.execution.intent_service import (
    ExecutionIntentGate,
    ExecutionIntentRequest,
    create_execution_intent,
)
from app.execution.service import (
    ExecutionProcessOutcome,
    ExecutionSubmitAck,
    PreSubmitReconciliation,
    process_execution_intent,
)
from app.execution.worker import claim_next_intent
from app.models import ExecutionIntent, ExecutionOrder
from app.models.ideas import TradeIdea
from app.models.risk import RiskSnapshot
from tests.conftest import idea_kwargs


NOW = datetime(2026, 8, 18, 21, 0, tzinfo=UTC)


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
    result = create_execution_intent(
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
    return result.intent


def _persist_submission_placeholder(session: Session, intent: ExecutionIntent) -> ExecutionOrder:
    order = ExecutionOrder(
        intent_id=intent.id,
        client_order_id=f"e-{intent.id.hex}",
        provider_order_id=None,
        side="BUY",
        order_type="ENTRY",
        status="SUBMITTING",
        quantity=intent.planned_quantity,
        limit_price=intent.planned_entry_price,
        stop_price=None,
        submitted_at=NOW,
        acknowledged_at=None,
    )
    session.add(order)
    session.flush()
    return order


class _BasePort:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.submit_client_order_ids: list[str] = []

    def reconcile_before_submit(self, intent: ExecutionIntent) -> PreSubmitReconciliation:
        self.calls.append("reconcile_before_submit")
        return PreSubmitReconciliation.absent()

    def submit(self, intent: ExecutionIntent, *, client_order_id: str) -> ExecutionSubmitAck:
        self.calls.append("submit")
        self.submit_client_order_ids.append(client_order_id)
        return ExecutionSubmitAck(
            provider_order_id="provider-entry-1",
            status="ACKNOWLEDGED",
            acknowledged_at=NOW,
        )

    def consume_fills(self, intent: ExecutionIntent, order: ExecutionOrder):
        self.calls.append("consume_fills")
        return []

    def arm_protection(self, intent, order, *, filled_quantity):
        raise AssertionError("protection is outside this SAI-027 test")

    def reconcile(self, intent: ExecutionIntent) -> None:
        raise AssertionError("post-protection reconcile is outside this SAI-027 test")

    def manage_until_close(self, intent: ExecutionIntent) -> None:
        raise AssertionError("management is outside this SAI-027 test")


class _TimeoutSubmitPort(_BasePort):
    def submit(self, intent: ExecutionIntent, *, client_order_id: str) -> ExecutionSubmitAck:
        self.calls.append("submit")
        self.submit_client_order_ids.append(client_order_id)
        raise TimeoutError("provider response timed out after request dispatch")


class _RecoveryPort(_BasePort):
    def __init__(self, reconciliation) -> None:
        super().__init__()
        self.reconciliation = reconciliation

    def reconcile_submission(self, intent: ExecutionIntent, order: ExecutionOrder):
        self.calls.append("reconcile_submission")
        return self.reconciliation

    def submit(self, intent: ExecutionIntent, *, client_order_id: str) -> ExecutionSubmitAck:
        raise AssertionError("recovery must not submit before authoritative reconciliation")


def _found():
    return SimpleNamespace(
        outcome="FOUND",
        provider_order_id="provider-entry-1",
        status="ACKNOWLEDGED",
        acknowledged_at=NOW,
        reason=None,
    )


def _absent():
    return SimpleNamespace(
        outcome="ABSENT",
        provider_order_id=None,
        status=None,
        acknowledged_at=None,
        reason=None,
    )


def _unknown(reason: str):
    return SimpleNamespace(
        outcome="UNKNOWN",
        provider_order_id=None,
        status=None,
        acknowledged_at=None,
        reason=reason,
    )


def test_submit_timeout_persists_one_order_and_moves_to_durable_ambiguous(session, instrument):
    intent = _seed_intent(session, instrument)
    port = _TimeoutSubmitPort()

    outcome = process_execution_intent(session, intent_id=intent.id, port=port, now=NOW)
    session.flush()

    assert isinstance(outcome, ExecutionProcessOutcome)
    assert outcome.processed is False
    assert "ambiguous" in (outcome.blocked_reason or "").lower()
    assert intent.state == ExecutionState.AMBIGUOUS
    assert intent.retry_count == 1
    assert intent.next_retry_at is not None
    assert intent.next_retry_at > NOW

    orders = session.query(ExecutionOrder).filter_by(intent_id=intent.id).all()
    assert len(orders) == 1
    assert orders[0].client_order_id == f"e-{intent.id.hex}"
    assert orders[0].provider_order_id is None
    assert orders[0].status == "SUBMITTING"
    assert port.submit_client_order_ids == [f"e-{intent.id.hex}"]


def test_restart_from_submitting_reconciles_found_order_without_resubmit(session, instrument):
    intent = _seed_intent(session, instrument)
    intent.state = ExecutionState.SUBMITTING
    order = _persist_submission_placeholder(session, intent)
    port = _RecoveryPort(_found())

    outcome = process_execution_intent(session, intent_id=intent.id, port=port, now=NOW)
    session.flush()

    assert outcome.processed is False
    assert "no fills" in (outcome.blocked_reason or "").lower()
    assert intent.state == ExecutionState.ACKNOWLEDGED
    assert order.provider_order_id == "provider-entry-1"
    assert order.status == "ACKNOWLEDGED"
    assert order.acknowledged_at == NOW
    assert port.calls == ["reconcile_submission", "consume_fills"]
    assert session.query(ExecutionOrder).filter_by(intent_id=intent.id).count() == 1


def test_authoritative_absent_requires_new_run_then_reuses_same_client_order_id(session, instrument):
    intent = _seed_intent(session, instrument)
    intent.state = ExecutionState.AMBIGUOUS
    order = _persist_submission_placeholder(session, intent)
    recovery_port = _RecoveryPort(_absent())

    first = process_execution_intent(
        session, intent_id=intent.id, port=recovery_port, now=NOW
    )
    session.flush()

    assert first.processed is False
    assert intent.state == ExecutionState.READY_TO_SUBMIT
    assert intent.next_retry_at is not None
    assert recovery_port.calls == ["reconcile_submission"]
    assert session.query(ExecutionOrder).filter_by(intent_id=intent.id).count() == 1

    retry_at = intent.next_retry_at + timedelta(seconds=1)
    intent.next_retry_at = retry_at - timedelta(seconds=1)
    submit_port = _BasePort()
    second = process_execution_intent(
        session, intent_id=intent.id, port=submit_port, now=retry_at
    )
    session.flush()

    assert second.processed is False
    assert intent.state == ExecutionState.ACKNOWLEDGED
    assert submit_port.submit_client_order_ids == [f"e-{intent.id.hex}"]
    assert session.query(ExecutionOrder).filter_by(intent_id=intent.id).count() == 1
    assert order.provider_order_id == "provider-entry-1"


def test_unknown_reconciliation_stays_fail_closed_and_schedules_retry(session, instrument):
    intent = _seed_intent(session, instrument)
    intent.state = ExecutionState.SUBMITTING
    _persist_submission_placeholder(session, intent)
    port = _RecoveryPort(_unknown("venue lookup unavailable"))

    outcome = process_execution_intent(session, intent_id=intent.id, port=port, now=NOW)
    session.flush()

    assert outcome.processed is False
    assert "venue lookup unavailable" in (outcome.blocked_reason or "")
    assert intent.state == ExecutionState.RECONCILING
    assert intent.retry_count == 1
    assert intent.next_retry_at is not None
    assert intent.next_retry_at > NOW
    assert port.calls == ["reconcile_submission"]


def test_claim_next_intent_respects_due_time_and_durable_lease(session, instrument):
    intent = _seed_intent(session, instrument)
    intent.state = ExecutionState.SUBMITTING
    intent.next_retry_at = NOW + timedelta(minutes=1)
    session.flush()

    assert claim_next_intent(session, worker_id="worker-a", now=NOW) is None

    intent.next_retry_at = NOW - timedelta(seconds=1)
    session.flush()
    claimed = claim_next_intent(session, worker_id="worker-a", now=NOW)

    assert claimed is not None
    assert claimed.id == intent.id
    assert intent.lease_owner == "worker-a"
    assert intent.lease_expires_at is not None
    assert intent.lease_expires_at > NOW

    # The same durable lease blocks another claim until it expires.
    assert claim_next_intent(session, worker_id="worker-b", now=NOW) is None

    intent.lease_expires_at = NOW - timedelta(seconds=1)
    session.flush()
    reclaimed = claim_next_intent(session, worker_id="worker-b", now=NOW)
    assert reclaimed is not None
    assert reclaimed.id == intent.id
    assert intent.lease_owner == "worker-b"
