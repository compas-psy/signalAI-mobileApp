from __future__ import annotations

from datetime import timedelta

from app.execution.enums import ExecutionState
from app.execution.service import ProtectionReconciliation, process_execution_intent
from tests.execution.test_protection_lifecycle import NOW, _ProtectionPort, _seed_intent


class _AmbiguousArmPort(_ProtectionPort):
    def arm_protection(self, intent, order, *, filled_quantity):
        self.calls.append("arm_protection")
        self.arm_quantities.append(filled_quantity)
        if self.calls.count("arm_protection") == 1:
            raise TimeoutError("timeout after stop submit")
        return super().arm_protection(
            intent,
            order,
            filled_quantity=filled_quantity,
        )


def test_ambiguous_stop_submit_reconciles_before_any_repair_submit(session, instrument):
    intent = _seed_intent(session, instrument)
    port = _AmbiguousArmPort(
        protection_result=ProtectionReconciliation.unknown("provider read unavailable")
    )

    first = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=NOW + timedelta(seconds=5),
    )
    assert first.processed is False
    assert intent.state == ExecutionState.PROTECTION_PENDING
    assert port.calls.count("arm_protection") == 1
    due = intent.next_retry_at
    assert due is not None

    second = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=due,
    )
    assert second.processed is False
    assert port.calls.count("reconcile_protection") == 1
    assert port.calls.count("arm_protection") == 1

    # Only an authoritative MISSING result makes a repair submission safe.
    port.protection_result = ProtectionReconciliation.missing("stop is absent")
    repair_due = intent.next_retry_at
    assert repair_due is not None
    third = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=repair_due,
    )
    assert third.processed is False
    assert port.calls.count("arm_protection") == 2
