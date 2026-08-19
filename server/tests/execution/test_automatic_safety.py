from __future__ import annotations

import importlib
from datetime import UTC, datetime

import pytest

from app.execution.enums import ExecutionKillSwitchLevel, ExecutionLifecycleMode
from app.execution.kill_switch import set_execution_kill_switch
from app.execution.mode import ModeChangeAuthorization, change_execution_mode, get_execution_mode
from app.models import ExecutionModeEvent, RiskState
from app.ops.backpressure import build_backpressure_plan
from app.ops.ollama_shed import OllamaShedResult, OllamaShedStatus
from app.ops.pressure import PressureAssessment, PressureState
from app.ops.remediation import record_resource_remediation
from app.ops.retention import RetentionResult, RetentionStatus

NOW = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)


def _safety():
    return importlib.import_module("app.execution.automatic_safety")


def _set_mode(session, target: ExecutionLifecycleMode) -> None:
    current = get_execution_mode(session).mode
    if current == target:
        return
    change_execution_mode(
        session,
        target=target,
        actor="test",
        reason="test setup",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test setup authorization",
            detail_json={"test_only": True},
        ),
    )
    session.flush()


def _assessment(state: PressureState) -> PressureAssessment:
    return PressureAssessment(
        state=state,
        score=100 if state is PressureState.CRITICAL else 5,
        reasons=("oom_kill_detected",) if state is PressureState.CRITICAL else ("memory_headroom_pressure",),
        active_dimensions=2,
    )


def _ollama() -> OllamaShedResult:
    return OllamaShedResult(
        status=OllamaShedStatus.UNLOADED,
        attempted=True,
        model="qwen3.5:4b",
        detail="UNLOADED",
    )


def _retention() -> RetentionResult:
    return RetentionResult(
        status=RetentionStatus.NOT_REQUIRED,
        candidate_files=0,
        candidate_bytes=0,
        deleted_files=0,
        deleted_bytes=0,
    )


def test_automatic_downshift_changes_mode_and_records_system_event(session):
    safety = _safety()
    _set_mode(session, ExecutionLifecycleMode.CANARY)
    before = session.query(ExecutionModeEvent).count()

    result = safety.automatic_downshift(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
        reason="execution health degraded",
    )
    session.flush()

    assert result.changed is True
    assert result.before == ExecutionLifecycleMode.CANARY
    assert result.after == ExecutionLifecycleMode.SANDBOX
    assert get_execution_mode(session).mode == ExecutionLifecycleMode.SANDBOX
    event = session.query(ExecutionModeEvent).order_by(ExecutionModeEvent.occurred_at.desc()).first()
    assert event is not None
    assert session.query(ExecutionModeEvent).count() == before + 1
    assert event.actor == "system"
    assert event.detail_json["direction"] == "lower-risk"


def test_automatic_downshift_never_allows_equal_or_higher_risk_target(session):
    safety = _safety()
    _set_mode(session, ExecutionLifecycleMode.SANDBOX)

    with pytest.raises(safety.AutomaticSafetyRejected, match="lower-risk"):
        safety.automatic_downshift(
            session,
            target=ExecutionLifecycleMode.CANARY,
            reason="forbidden auto promotion",
        )
    with pytest.raises(safety.AutomaticSafetyRejected, match="lower-risk"):
        safety.automatic_downshift(
            session,
            target=ExecutionLifecycleMode.SANDBOX,
            reason="not a downshift",
        )

    assert get_execution_mode(session).mode == ExecutionLifecycleMode.SANDBOX


def test_automatic_halt_sets_exact_halt_level_but_never_weakens_stronger_switch(session):
    safety = _safety()

    first = safety.automatic_halt_new_entries(
        session,
        reason="critical resource pressure",
    )
    assert first.changed is True
    state = session.get(RiskState, 1)
    assert state is not None
    assert state.kill_switch is True
    assert state.kill_switch_level == ExecutionKillSwitchLevel.HALT_NEW_ENTRIES

    set_execution_kill_switch(
        session,
        level=ExecutionKillSwitchLevel.CANCEL_PENDING_ENTRIES,
        actor="owner",
        reason="owner escalated",
    )
    stronger = safety.automatic_halt_new_entries(
        session,
        reason="critical resource pressure continues",
    )

    assert stronger.changed is False
    state = session.get(RiskState, 1)
    assert state is not None
    assert state.kill_switch_level == ExecutionKillSwitchLevel.CANCEL_PENDING_ENTRIES


def test_critical_resource_remediation_now_applies_real_halt(session):
    session.add(RiskState(id=1))
    session.flush()

    result = record_resource_remediation(
        session,
        assessment=_assessment(PressureState.CRITICAL),
        plan=build_backpressure_plan(state=PressureState.CRITICAL),
        ollama=_ollama(),
        retention=_retention(),
        now=NOW,
    )
    session.flush()

    assert result.recorded is True
    state = session.get(RiskState, 1)
    assert state is not None
    assert state.kill_switch is True
    assert state.kill_switch_level == ExecutionKillSwitchLevel.HALT_NEW_ENTRIES


def test_noncritical_resource_pressure_never_halts_entries(session):
    session.add(RiskState(id=1))
    session.flush()

    record_resource_remediation(
        session,
        assessment=_assessment(PressureState.PRESSURE),
        plan=build_backpressure_plan(state=PressureState.PRESSURE),
        ollama=_ollama(),
        retention=_retention(),
        now=NOW,
    )
    session.flush()

    state = session.get(RiskState, 1)
    assert state is not None
    assert state.kill_switch is False
