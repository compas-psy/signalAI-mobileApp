from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.execution.intent_service import (
    ExecutionIntentGate,
    ExecutionIntentRequest,
    ExecutionIntentRejected,
    create_execution_intent,
)
from app.models import ExecutionIntent, RiskSnapshot, TradeIdea
from tests.conftest import idea_kwargs


def _fixtures(session, instrument, now):
    idea = TradeIdea(**idea_kwargs(instrument.instrument_id, now))
    risk = RiskSnapshot(risk_equity=Decimal("100000"))
    session.add_all([idea, risk])
    session.flush()
    return idea, risk


def _request(idea: TradeIdea, risk: RiskSnapshot) -> ExecutionIntentRequest:
    return ExecutionIntentRequest(
        idea_id=idea.id,
        instrument_id=idea.instrument_id,
        strategy_version=idea.strategy_version,
        risk_policy_snapshot_id=risk.id,
        risk_override_id=None,
        venue="MOEX",
        account="sandbox-main",
        planned_quantity=idea.quantity,
        planned_entry_price=idea.entry_reference,
        planned_stop_price=idea.stop,
    )


def _approved_gate() -> ExecutionIntentGate:
    return ExecutionIntentGate(
        owner_approved=True,
        risk_snapshot_verified=True,
        mode_allows_intent=True,
        kill_switch_clear=True,
        venue_capability_verified=True,
    )


@pytest.mark.parametrize(
    "field",
    [
        "owner_approved",
        "risk_snapshot_verified",
        "mode_allows_intent",
        "kill_switch_clear",
        "venue_capability_verified",
    ],
)
def test_intent_creation_fails_closed_when_any_required_gate_is_missing(
    session, instrument, now, field
):
    idea, risk = _fixtures(session, instrument, now)
    request = _request(idea, risk)
    gate = replace(_approved_gate(), **{field: False})

    with pytest.raises(ExecutionIntentRejected, match=field):
        create_execution_intent(session, request=request, gate=gate)

    assert session.scalar(select(func.count()).select_from(ExecutionIntent)) == 0


def test_same_stable_identity_is_idempotent_in_postgres(session, instrument, now):
    idea, risk = _fixtures(session, instrument, now)
    request = _request(idea, risk)

    first = create_execution_intent(session, request=request, gate=_approved_gate())
    second = create_execution_intent(session, request=request, gate=_approved_gate())

    assert first.created is True
    assert second.created is False
    assert first.intent.id == second.intent.id
    assert first.intent.identity_hash == second.intent.identity_hash
    assert session.scalar(select(func.count()).select_from(ExecutionIntent)) == 1


def test_intent_persists_exact_plan_and_starts_without_submission(
    session, instrument, now
):
    idea, risk = _fixtures(session, instrument, now)

    result = create_execution_intent(
        session,
        request=_request(idea, risk),
        gate=_approved_gate(),
    )
    session.flush()

    row = session.get(ExecutionIntent, result.intent.id)
    assert row is not None
    assert str(row.state) == "INTENT_CREATED"
    assert row.idea_id == idea.id
    assert row.strategy_version == idea.strategy_version
    assert row.risk_policy_snapshot_id == risk.id
    assert row.venue == "MOEX"
    assert row.account == "sandbox-main"
    assert row.planned_quantity == Decimal("1")
    assert row.planned_entry_price == Decimal("90100")
    assert row.planned_stop_price == Decimal("89400")


def test_identity_changes_when_execution_destination_changes(session, instrument, now):
    idea, risk = _fixtures(session, instrument, now)
    original = _request(idea, risk)

    first = create_execution_intent(session, request=original, gate=_approved_gate())
    second = create_execution_intent(
        session,
        request=replace(original, account="sandbox-secondary"),
        gate=_approved_gate(),
    )

    assert first.intent.id != second.intent.id
    assert first.intent.identity_hash != second.intent.identity_hash
    assert session.scalar(select(func.count()).select_from(ExecutionIntent)) == 2
