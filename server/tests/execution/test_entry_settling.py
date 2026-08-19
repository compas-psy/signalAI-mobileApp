from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

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
    ProtectionReconciliation,
    SubmissionReconciliation,
    process_execution_intent,
)
from app.models import ExecutionIntent, ExecutionOrder, ExecutionProtection
from app.models.ideas import TradeIdea
from app.models.risk import RiskSnapshot
from tests.conftest import idea_kwargs


NOW = datetime(2026, 8, 19, 16, 0, tzinfo=UTC)


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


def _fill(fill_id: str, quantity: str, *, at: datetime) -> ExecutionFillSnapshot:
    return ExecutionFillSnapshot(
        provider_fill_id=fill_id,
        quantity=Decimal(quantity),
        price=Decimal("90110"),
        fee_amount=Decimal("1"),
        fee_currency="RUB",
        filled_at=at,
    )


class _SettlingPort(ExecutionPort):
    def __init__(
        self,
        *,
        fill_batches: list[list[ExecutionFillSnapshot]],
        entry_statuses: list[str],
        fail_expansion: bool = False,
    ) -> None:
        self.fill_batches = list(fill_batches)
        self.entry_statuses = list(entry_statuses)
        self.fail_expansion = fail_expansion
        self.covered_quantity = Decimal("0")
        self.calls: list[str] = []
        self.arm_quantities: list[Decimal] = []
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
        status = self.entry_statuses.pop(0) if self.entry_statuses else "FILLED"
        return SubmissionReconciliation.found(
            provider_order_id=order.provider_order_id or "provider-entry-1",
            status=status,
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
        if not self.fill_batches:
            return []
        return self.fill_batches.pop(0)

    def arm_protection(
        self,
        intent: ExecutionIntent,
        order: ExecutionOrder,
        *,
        filled_quantity: Decimal,
    ) -> ExecutionProtectionAck:
        self.calls.append("arm_protection")
        self.arm_quantities.append(Decimal(filled_quantity))
        if self.fail_expansion and Decimal(filled_quantity) > Decimal("1"):
            raise TimeoutError("expansion submit is ambiguous")
        self.covered_quantity = Decimal(filled_quantity)
        return ExecutionProtectionAck(
            provider_order_id=f"provider-stop-{filled_quantity}",
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
        if self.covered_quantity < Decimal(protection.quantity):
            return ProtectionReconciliation.missing(
                f"covered {self.covered_quantity} < target {protection.quantity}"
            )
        return ProtectionReconciliation.matched(
            provider_order_id=protection.provider_order_id or "provider-stop-adopted",
            status="ACTIVE",
            quantity=Decimal(protection.quantity),
            stop_price=Decimal(protection.stop_price),
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
        self.flatten_quantities.append(Decimal(filled_quantity))
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
        return SubmissionReconciliation.unknown("close pending")

    def reconcile(self, intent: ExecutionIntent) -> None:
        self.calls.append("reconcile")

    def manage_until_close(self, intent: ExecutionIntent) -> None:
        self.calls.append("manage_until_close")


def test_partial_fill_remains_in_entry_settling_until_entry_is_terminal(
    session, instrument
):
    intent = _seed_intent(session, instrument)
    port = _SettlingPort(
        fill_batches=[[_fill("fill-1", "1", at=NOW)]],
        entry_statuses=["PARTIALLY_FILLED"],
    )

    outcome = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=NOW + timedelta(seconds=1),
    )
    session.flush()

    assert outcome.processed is False
    assert intent.state == ExecutionState.PROTECTION_PENDING
    assert port.arm_quantities == [Decimal("1")]
    assert port.calls.count("reconcile_submission") == 1
    assert "manage_until_close" not in port.calls


def test_late_fill_expands_protection_before_terminal_entry_can_manage(
    session, instrument
):
    intent = _seed_intent(session, instrument)
    port = _SettlingPort(
        fill_batches=[
            [_fill("fill-1", "1", at=NOW)],
            [
                _fill("fill-1", "1", at=NOW),
                _fill("fill-2", "1", at=NOW + timedelta(seconds=10)),
            ],
        ],
        entry_statuses=["PARTIALLY_FILLED", "FILLED"],
    )

    first = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=NOW + timedelta(seconds=1),
    )
    assert first.processed is False
    assert intent.state == ExecutionState.PROTECTION_PENDING
    due = intent.next_retry_at
    assert due is not None

    second = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=max(due, NOW + timedelta(seconds=11)),
    )
    session.flush()

    assert second.processed is True
    assert intent.state == ExecutionState.MANAGING
    assert port.arm_quantities == [Decimal("1"), Decimal("2")]
    protection = session.query(ExecutionProtection).filter_by(intent_id=intent.id).one()
    assert protection.quantity == Decimal("2")
    assert "manage_until_close" in port.calls


def test_late_uncovered_fill_gets_its_own_protection_sla_window(
    session, instrument
):
    intent = _seed_intent(session, instrument)
    late_fill_at = NOW + timedelta(seconds=60)
    port = _SettlingPort(
        fill_batches=[
            [_fill("fill-1", "1", at=NOW)],
            [
                _fill("fill-1", "1", at=NOW),
                _fill("fill-2", "1", at=late_fill_at),
            ],
            [
                _fill("fill-1", "1", at=NOW),
                _fill("fill-2", "1", at=late_fill_at),
            ],
        ],
        entry_statuses=["PARTIALLY_FILLED", "FILLED", "FILLED"],
        fail_expansion=True,
    )

    first = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=NOW + timedelta(seconds=1),
    )
    assert first.processed is False
    due = intent.next_retry_at
    assert due is not None

    second = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=max(due, late_fill_at + timedelta(seconds=1)),
    )
    session.flush()

    assert second.processed is False
    assert intent.state == ExecutionState.PROTECTION_PENDING
    assert port.flatten_quantities == []
    due = intent.next_retry_at
    assert due is not None

    third = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=max(due, late_fill_at + timedelta(seconds=31)),
    )
    session.flush()

    assert third.processed is False
    assert intent.state == ExecutionState.EMERGENCY_FLATTEN
    assert port.flatten_quantities == [Decimal("2")]
