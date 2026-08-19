from __future__ import annotations

from datetime import timedelta

from app.execution.enums import ExecutionState
from app.execution.service import (
    ExecutionProtectionAck,
    ProtectionReconciliation,
    process_execution_intent,
)
from tests.execution.test_protection_lifecycle import NOW, _ProtectionPort, _seed_intent


class _AmbiguousArmPort(_ProtectionPort):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.arm_attempts = 0

    def arm_protection(self, intent, order, *, filled_quantity):
        self.arm_attempts += 1
        self.calls.append("arm_protection")
        self.arm_quantities.append(filled_quantity)
        if self.arm_attempts == 1:
            raise TimeoutError("timeout after stop submit")
        return ExecutionProtectionAck(
            provider_order_id="provider-stop-repair",
            status="ACTIVE",
            armed_at=NOW,
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
    assert port.arm_attempts == 1
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
    assert port.arm_attempts == 1

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
    assert port.arm_attempts == 2
