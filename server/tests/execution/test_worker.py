from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session, sessionmaker

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
    ProtectionReconciliation,
    SubmissionReconciliation,
    process_execution_intent,
)
from app.execution.worker import (
    DisabledExecutionPort,
    execution_tuple_lock,
)
from app.models import ExecutionFill, ExecutionIntent, ExecutionOrder, ExecutionProtection
from app.models.enums import Direction
from app.models.ideas import TradeIdea
from app.models.risk import RiskSnapshot
from tests.conftest import idea_kwargs


NOW = datetime(2026, 8, 18, 20, 0, tzinfo=UTC)


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


class _HappyPort(ExecutionPort):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def reconcile_before_submit(self, intent: ExecutionIntent) -> PreSubmitReconciliation:
        self.calls.append("reconcile_before_submit")
        return PreSubmitReconciliation.absent()

    def reconcile_submission(
        self, intent: ExecutionIntent, order: ExecutionOrder
    ) -> SubmissionReconciliation:
        self.calls.append("reconcile_submission")
        return SubmissionReconciliation.unknown("not used in happy path")

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
        self, intent: ExecutionIntent, order: ExecutionOrder, *, filled_quantity: Decimal
    ) -> ExecutionProtectionAck:
        self.calls.append("arm_protection")
        return ExecutionProtectionAck(
            provider_order_id="provider-stop-1",
            status="ACTIVE",
            armed_at=NOW,
        )

    def reconcile_protection(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
        protection: ExecutionProtection,
    ) -> ProtectionReconciliation:
        self.calls.append("reconcile_protection")
        return ProtectionReconciliation.matched(
            provider_order_id="provider-stop-1",
            status="ACTIVE",
            quantity=Decimal("1"),
            stop_price=Decimal("89400"),
            reconciled_at=NOW,
        )

    def emergency_flatten(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
        *,
        filled_quantity: Decimal,
        client_order_id: str,
    ) -> ExecutionSubmitAck:
        self.calls.append("emergency_flatten")
        return ExecutionSubmitAck(
            provider_order_id="provider-emergency-1",
            status="ACKNOWLEDGED",
            acknowledged_at=NOW,
        )

    def reconcile_emergency_flatten(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
    ) -> SubmissionReconciliation:
        self.calls.append("reconcile_emergency_flatten")
        return SubmissionReconciliation.unknown("not used in happy path")

    def reconcile(self, intent: ExecutionIntent) -> None:
        self.calls.append("reconcile")

    def manage_until_close(self, intent: ExecutionIntent) -> None:
        self.calls.append("manage_until_close")


class _UnknownBeforeSubmit(_HappyPort):
    def reconcile_before_submit(self, intent: ExecutionIntent) -> PreSubmitReconciliation:
        self.calls.append("reconcile_before_submit")
        return PreSubmitReconciliation.unknown("venue unavailable")


def test_worker_executes_required_b53_order_and_persists_ack_fill_protection(
    session, instrument
):
    intent = _seed_intent(session, instrument)
    port = _HappyPort()

    outcome = process_execution_intent(session, intent_id=intent.id, port=port)
    session.flush()

    assert outcome.processed is True
    assert outcome.blocked_reason is None
    assert port.calls == [
        "reconcile_before_submit",
        "submit",
        "consume_fills",
        "arm_protection",
        "reconcile_protection",
        "reconcile",
        "manage_until_close",
    ]
    assert str(intent.state) == "MANAGING"

    order = session.query(ExecutionOrder).filter_by(intent_id=intent.id).one()
    assert order.client_order_id == f"e-{intent.id.hex}"
    assert order.provider_order_id == "provider-entry-1"
    assert order.status == "ACKNOWLEDGED"

    fill = session.query(ExecutionFill).filter_by(intent_id=intent.id).one()
    assert fill.provider_fill_id == "fill-1"
    assert fill.quantity == Decimal("1")
    assert fill.price == Decimal("90110")

    protection = session.query(ExecutionProtection).filter_by(intent_id=intent.id).one()
    assert protection.provider_order_id == "provider-stop-1"
    assert protection.quantity == Decimal("1")
    assert protection.stop_price == Decimal("89400")
    assert protection.status == "ACTIVE"
    assert protection.last_reconciled_at == NOW


def test_unknown_pre_submit_reconciliation_fails_closed_without_submit(session, instrument):
    intent = _seed_intent(session, instrument)
    port = _UnknownBeforeSubmit()

    outcome = process_execution_intent(session, intent_id=intent.id, port=port)
    session.flush()

    assert outcome.processed is False
    assert "venue unavailable" in outcome.blocked_reason
    assert port.calls == ["reconcile_before_submit"]
    assert session.query(ExecutionOrder).filter_by(intent_id=intent.id).count() == 0
    assert str(intent.state) == "READY_TO_SUBMIT"


def test_execution_tuple_advisory_lock_blocks_same_tuple_but_not_other_instrument(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    first = factory()
    second = factory()
    try:
        with execution_tuple_lock(
            first, venue="MOEX", account="sandbox-main", instrument_id="MOEX:FUT:SIU6"
        ) as first_acquired:
            assert first_acquired is True
            with execution_tuple_lock(
                second,
                venue="MOEX",
                account="sandbox-main",
                instrument_id="MOEX:FUT:SIU6",
            ) as duplicate_acquired:
                assert duplicate_acquired is False
            with execution_tuple_lock(
                second,
                venue="MOEX",
                account="sandbox-main",
                instrument_id="MOEX:FUT:BRU6",
            ) as other_acquired:
                assert other_acquired is True
    finally:
        first.close()
        second.close()


def test_disabled_production_port_never_submits_orders(session, instrument):
    intent = _seed_intent(session, instrument)
    port = DisabledExecutionPort()

    outcome = process_execution_intent(session, intent_id=intent.id, port=port)

    assert outcome.processed is False
    assert "adapter" in outcome.blocked_reason.lower()
    assert session.query(ExecutionOrder).filter_by(intent_id=intent.id).count() == 0
