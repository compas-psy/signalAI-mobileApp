from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

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
from app.execution.enums import ExecutionState
from app.models import ExecutionIntent, ExecutionOrder, ExecutionProtection
from app.models.ideas import TradeIdea
from app.models.risk import RiskSnapshot
from tests.conftest import idea_kwargs


NOW = datetime(2026, 8, 19, 14, 30, tzinfo=UTC)


def _seed_intent(
    session: Session,
    instrument,
    *,
    planned_quantity: Decimal = Decimal("2"),
) -> ExecutionIntent:
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
            planned_quantity=planned_quantity,
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


class _ProtectionPort(ExecutionPort):
    def __init__(
        self,
        *,
        fill_quantity: Decimal = Decimal("1"),
        protection_result: ProtectionReconciliation | None = None,
        flatten_reconciliation: SubmissionReconciliation | None = None,
        flatten_error: Exception | None = None,
    ) -> None:
        self.fill_quantity = fill_quantity
        self.protection_result = protection_result or ProtectionReconciliation.matched(
            provider_order_id="provider-stop-1",
            status="ACTIVE",
            quantity=fill_quantity,
            stop_price=Decimal("89400"),
            reconciled_at=NOW,
        )
        self.flatten_reconciliation = flatten_reconciliation or SubmissionReconciliation.unknown(
            "emergency close still pending"
        )
        self.flatten_error = flatten_error
        self.calls: list[str] = []
        self.arm_quantities: list[Decimal] = []
        self.flatten_client_ids: list[str] = []
        self.flatten_quantities: list[Decimal] = []

    def reconcile_before_submit(self, intent: ExecutionIntent) -> PreSubmitReconciliation:
        self.calls.append("reconcile_before_submit")
        return PreSubmitReconciliation.absent()

    def reconcile_submission(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
    ) -> SubmissionReconciliation:
        self.calls.append("reconcile_submission")
        return SubmissionReconciliation.unknown("entry reconciliation is not used here")

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
                quantity=self.fill_quantity,
                price=Decimal("90110"),
                fee_amount=Decimal("2.5"),
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
        self.arm_quantities.append(filled_quantity)
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
        return self.protection_result

    def emergency_flatten(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
        *,
        filled_quantity: Decimal,
        client_order_id: str,
    ) -> ExecutionSubmitAck:
        self.calls.append("emergency_flatten")
        self.flatten_client_ids.append(client_order_id)
        self.flatten_quantities.append(filled_quantity)
        if self.flatten_error is not None:
            raise self.flatten_error
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
        return self.flatten_reconciliation

    def reconcile(self, intent: ExecutionIntent) -> None:
        self.calls.append("reconcile")

    def manage_until_close(self, intent: ExecutionIntent) -> None:
        self.calls.append("manage_until_close")


def test_partial_fill_is_protected_only_for_actual_quantity_and_only_after_reconciliation(
    session, instrument
):
    intent = _seed_intent(session, instrument, planned_quantity=Decimal("2"))
    port = _ProtectionPort(fill_quantity=Decimal("1"))

    outcome = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=NOW,
    )
    session.flush()

    assert outcome.processed is False
    assert port.arm_quantities == [Decimal("1")]
    assert port.calls.index("reconcile_protection") > port.calls.index("arm_protection")
    assert "manage_until_close" not in port.calls
    assert intent.state == ExecutionState.PROTECTION_PENDING

    protection = session.query(ExecutionProtection).filter_by(intent_id=intent.id).one()
    assert protection.quantity == Decimal("1")
    assert protection.stop_price == Decimal("89400")
    assert protection.status == "ACTIVE"
    assert protection.last_reconciled_at == NOW


def test_unconfirmed_protection_before_sla_stays_pending_and_does_not_manage(
    session, instrument
):
    intent = _seed_intent(session, instrument)
    port = _ProtectionPort(
        protection_result=ProtectionReconciliation.unknown("provider read timed out")
    )

    outcome = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=NOW + timedelta(seconds=10),
    )
    session.flush()

    assert outcome.processed is False
    assert "provider read timed out" in (outcome.blocked_reason or "")
    assert intent.state == ExecutionState.PROTECTION_PENDING
    assert intent.next_retry_at is not None
    assert "manage_until_close" not in port.calls
    protection = session.query(ExecutionProtection).filter_by(intent_id=intent.id).one()
    assert protection.status == "UNCONFIRMED"
    assert protection.provider_order_id == "provider-stop-1"


def test_pending_protection_retry_reconciles_without_blindly_arming_second_stop(
    session, instrument
):
    intent = _seed_intent(session, instrument, planned_quantity=Decimal("1"))
    port = _ProtectionPort(
        protection_result=ProtectionReconciliation.unknown("temporary ambiguity")
    )

    first = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=NOW + timedelta(seconds=5),
    )
    assert first.processed is False
    due = intent.next_retry_at
    assert due is not None

    port.protection_result = ProtectionReconciliation.matched(
        provider_order_id="provider-stop-1",
        status="ACTIVE",
        quantity=Decimal("1"),
        stop_price=Decimal("89400"),
        reconciled_at=due,
    )
    second = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=due,
    )
    session.flush()

    assert second.processed is True
    assert port.calls.count("arm_protection") == 1
    assert port.calls.count("reconcile_protection") == 2
    assert intent.state == ExecutionState.MANAGING


def test_protection_still_unknown_after_30_seconds_enters_durable_emergency_flatten(
    session, instrument
):
    intent = _seed_intent(session, instrument)
    port = _ProtectionPort(
        protection_result=ProtectionReconciliation.unknown("cannot confirm stop")
    )

    outcome = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=NOW + timedelta(seconds=31),
    )
    session.flush()

    assert outcome.processed is False
    assert intent.state == ExecutionState.EMERGENCY_FLATTEN
    assert port.flatten_client_ids == [f"x-{intent.id.hex}"]
    assert port.flatten_quantities == [Decimal("1")]
    assert "manage_until_close" not in port.calls

    emergency = (
        session.query(ExecutionOrder)
        .filter_by(intent_id=intent.id, order_type="EMERGENCY_FLATTEN")
        .one()
    )
    assert emergency.client_order_id == f"x-{intent.id.hex}"
    assert emergency.provider_order_id == "provider-emergency-1"
    assert emergency.status == "ACKNOWLEDGED"
    assert emergency.quantity == Decimal("1")


def test_ambiguous_emergency_flatten_is_reconciled_before_close_without_second_submit(
    session, instrument
):
    intent = _seed_intent(session, instrument)
    port = _ProtectionPort(
        protection_result=ProtectionReconciliation.unknown("cannot confirm stop"),
        flatten_error=TimeoutError("provider timeout after close submit"),
    )

    first = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=NOW + timedelta(seconds=31),
    )
    session.flush()

    assert first.processed is False
    assert intent.state == ExecutionState.EMERGENCY_FLATTEN
    emergency = (
        session.query(ExecutionOrder)
        .filter_by(intent_id=intent.id, order_type="EMERGENCY_FLATTEN")
        .one()
    )
    assert emergency.status == "AMBIGUOUS"
    assert port.calls.count("emergency_flatten") == 1
    due = intent.next_retry_at
    assert due is not None

    port.flatten_error = None
    port.flatten_reconciliation = SubmissionReconciliation.found(
        provider_order_id="provider-emergency-1",
        status="FILLED",
        acknowledged_at=due,
    )
    second = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=due,
    )
    session.flush()

    assert second.processed is True
    assert port.calls.count("emergency_flatten") == 1
    assert port.calls.count("reconcile_emergency_flatten") == 1
    assert intent.state == ExecutionState.CLOSED
    assert emergency.provider_order_id == "provider-emergency-1"
    assert emergency.status == "FILLED"