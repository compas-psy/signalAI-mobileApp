from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.execution.enums import ExecutionLifecycleMode, ExecutionState
from app.execution.intent_service import (
    ExecutionIntentGate,
    ExecutionIntentRequest,
    create_execution_intent,
)
from app.execution.management_policy import (
    ManagementPolicySnapshotRejected,
    freeze_management_policy_snapshot,
)
from app.models import ExecutionManagementPolicySnapshot, ExecutionRiskOverride
from app.models.ideas import TradeIdea
from app.models.risk import RiskSnapshot
from tests.conftest import idea_kwargs


def _seed_protected_intent(
    session: Session,
    instrument,
    *,
    with_override: bool = False,
):
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            status="TRIGGERED",
            quality_status="PASS",
            score=Decimal("82"),
        )
    )
    risk = RiskSnapshot(
        risk_equity=Decimal("100000"),
        open_risk=Decimal("0.01"),
        binding_limit="cluster",
        cluster_risk_json={"crypto": "0.02"},
        detail_json={"policy_version": "risk-v7"},
    )
    session.add_all([idea, risk])
    session.flush()

    override = None
    quantity = Decimal("2")
    if with_override:
        override = ExecutionRiskOverride(
            idea_id=idea.id,
            risk_snapshot_id=risk.id,
            preset="BOOST_1",
            venue="MOEX",
            account="sandbox-main",
            execution_mode_snapshot=ExecutionLifecycleMode.PAPER,
            base_risk_pct=Decimal("0.005"),
            effective_risk_pct=Decimal("0.00625"),
            hard_cap_risk_pct=Decimal("0.01"),
            base_quantity=Decimal("1.6"),
            effective_quantity=quantity,
            effective_leverage=None,
            hard_cap_leverage=None,
            preview_hash="a" * 64,
            idempotency_key="sai-049-override",
            actor="owner",
            reason="bounded owner boost",
            detail_json={"binding": "manual_envelope"},
        )
        session.add(override)
        session.flush()

    creation = create_execution_intent(
        session,
        request=ExecutionIntentRequest(
            idea_id=idea.id,
            instrument_id=idea.instrument_id,
            strategy_version=idea.strategy_version,
            risk_policy_snapshot_id=risk.id,
            risk_override_id=override.id if override is not None else None,
            venue="MOEX",
            account="sandbox-main",
            planned_quantity=quantity,
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
    creation.intent.state = ExecutionState.PROTECTED
    session.flush()
    return creation.intent, idea, risk, override


def test_sai_049_freezes_strategy_risk_exit_override_and_venue_rules(session, instrument):
    intent, idea, risk, override = _seed_protected_intent(
        session,
        instrument,
        with_override=True,
    )
    exit_profile = {
        "profile_version": "exit-v3",
        "initial_stop": "89400",
        "targets": ["91000", "92000", "93000"],
        "trailing": "after_tp1",
    }
    venue_rules = {
        "rules_version": "moex-core-v2",
        "reduce_only_exit": True,
        "stop_tighten_only": True,
    }

    result = freeze_management_policy_snapshot(
        session,
        intent_id=intent.id,
        exit_profile=exit_profile,
        venue_rules=venue_rules,
    )
    session.flush()

    assert result.created is True
    snapshot = result.snapshot
    assert snapshot.intent_id == intent.id
    assert snapshot.strategy_version == idea.strategy_version
    assert snapshot.risk_policy_snapshot_id == risk.id
    assert snapshot.risk_override_id == override.id
    assert snapshot.exit_profile_json == exit_profile
    assert snapshot.venue_rules_json == venue_rules
    assert snapshot.risk_policy_json == {
        "risk_equity": "100000.00000000",
        "open_risk": "0.01000000",
        "binding_limit": "cluster",
        "entries_blocked": False,
        "halted": False,
        "cluster_risk": {"crypto": "0.02"},
        "detail": {"policy_version": "risk-v7"},
    }
    assert snapshot.manual_override_json["preset"] == "BOOST_1"
    assert snapshot.manual_override_json["effective_risk_pct"] == "0.00625000"
    assert snapshot.manual_override_json["effective_quantity"] == "2.000000000000"
    assert len(snapshot.content_hash) == 64


def test_sai_049_same_snapshot_is_idempotent_but_optimizer_cannot_rewrite_it(session, instrument):
    intent, _idea, _risk, _override = _seed_protected_intent(session, instrument)
    first = freeze_management_policy_snapshot(
        session,
        intent_id=intent.id,
        exit_profile={"profile_version": "exit-v1", "initial_stop": "89400"},
        venue_rules={"rules_version": "core-v1", "stop_tighten_only": True},
    )
    session.flush()

    replay = freeze_management_policy_snapshot(
        session,
        intent_id=intent.id,
        exit_profile={"profile_version": "exit-v1", "initial_stop": "89400"},
        venue_rules={"rules_version": "core-v1", "stop_tighten_only": True},
    )
    assert replay.created is False
    assert replay.snapshot.id == first.snapshot.id

    with pytest.raises(ManagementPolicySnapshotRejected, match="already frozen"):
        freeze_management_policy_snapshot(
            session,
            intent_id=intent.id,
            exit_profile={"profile_version": "exit-v99", "initial_stop": "89000"},
            venue_rules={"rules_version": "core-v99", "stop_tighten_only": False},
        )


def test_sai_049_cannot_freeze_before_position_is_protected(session, instrument):
    intent, _idea, _risk, _override = _seed_protected_intent(session, instrument)
    intent.state = ExecutionState.PROTECTION_PENDING
    session.flush()

    with pytest.raises(ManagementPolicySnapshotRejected, match="PROTECTED"):
        freeze_management_policy_snapshot(
            session,
            intent_id=intent.id,
            exit_profile={"profile_version": "exit-v1"},
            venue_rules={"rules_version": "core-v1"},
        )


def test_sai_049_snapshot_table_is_database_append_only(session):
    rows = session.execute(
        text(
            """
            SELECT c.relname, t.tgname
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            WHERE NOT t.tgisinternal
              AND t.tgname = 'execution_management_policy_snapshots_append_only'
            """
        )
    ).all()
    assert rows == [
        (
            ExecutionManagementPolicySnapshot.__tablename__,
            "execution_management_policy_snapshots_append_only",
        )
    ]
