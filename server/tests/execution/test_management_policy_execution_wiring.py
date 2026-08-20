from __future__ import annotations

from sqlalchemy import select

from app.execution.enums import ExecutionState
from app.execution.service import process_execution_intent
from app.models import ExecutionManagementPolicySnapshot
from tests.execution.test_entry_settling import NOW, _SettlingPort, _fill, _seed_intent


def test_sai_049_execution_freezes_management_policy_before_managing(session, instrument):
    intent = _seed_intent(session, instrument, planned_quantity=1)
    port = _SettlingPort(
        fill_batches=[[_fill("fill-1", "1", at=NOW)]],
        entry_statuses=["FILLED"],
    )

    outcome = process_execution_intent(
        session,
        intent_id=intent.id,
        port=port,
        now=NOW,
    )
    session.flush()

    assert outcome.processed is True
    assert intent.state == ExecutionState.MANAGING
    snapshot = session.execute(
        select(ExecutionManagementPolicySnapshot).where(
            ExecutionManagementPolicySnapshot.intent_id == intent.id
        )
    ).scalar_one()
    assert snapshot.exit_profile_json["profile_version"] == "signalai-idea-plan-v1"
    assert snapshot.exit_profile_json["initial_stop"] == "89400.000000000000"
    assert snapshot.venue_rules_json["scope"] == "SIGNALAI_CORE"
    assert snapshot.venue_rules_json["protection_required"] is True
    assert snapshot.venue_rules_json["stop_tighten_only"] is True
    assert snapshot.venue_rules_json["reduce_only_exit"] is True
