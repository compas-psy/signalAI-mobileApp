from __future__ import annotations

import contextlib

import pytest

from app.execution.enums import ExecutionKillSwitchLevel, ExecutionLifecycleMode
from app.execution.kill_switch import (
    get_execution_kill_switch_level,
    set_execution_kill_switch,
)
from app.execution.mode import ModeChangeAuthorization, change_execution_mode, get_execution_mode


def _set_mode(session, target: ExecutionLifecycleMode) -> None:
    current = get_execution_mode(session).mode
    if current == target:
        return
    change_execution_mode(
        session,
        target=target,
        actor="test",
        reason="automatic safety sequence setup",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test setup authorization",
            detail_json={"test_only": True},
        ),
    )
    session.flush()


def test_halt_then_downshift_persists_both_safety_states(session) -> None:
    from app.execution.automatic_safety import automatic_halt_and_downshift

    _set_mode(session, ExecutionLifecycleMode.CANARY)

    result = automatic_halt_and_downshift(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
        reason="canary structural failure requires owner recovery",
    )

    assert result.halt.before is ExecutionKillSwitchLevel.CLEAR
    assert result.halt.after is ExecutionKillSwitchLevel.HALT_NEW_ENTRIES
    assert result.halt.changed is True
    assert result.downshift.before is ExecutionLifecycleMode.CANARY
    assert result.downshift.after is ExecutionLifecycleMode.SANDBOX
    assert result.downshift.changed is True
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.HALT_NEW_ENTRIES
    assert get_execution_mode(session).mode is ExecutionLifecycleMode.SANDBOX

    # Reload authoritative rows instead of trusting ORM identity-map state.
    session.expire_all()
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.HALT_NEW_ENTRIES
    assert get_execution_mode(session).mode is ExecutionLifecycleMode.SANDBOX


def test_sequence_never_weakens_stronger_owner_switch(session) -> None:
    from app.execution.automatic_safety import automatic_halt_and_downshift

    _set_mode(session, ExecutionLifecycleMode.CANARY)
    set_execution_kill_switch(
        session,
        level=ExecutionKillSwitchLevel.CANCEL_PENDING_ENTRIES,
        actor="owner",
        reason="owner escalated before automatic demotion",
    )

    result = automatic_halt_and_downshift(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
        reason="canary ambiguity requires downshift",
    )

    assert result.halt.before is ExecutionKillSwitchLevel.CANCEL_PENDING_ENTRIES
    assert result.halt.after is ExecutionKillSwitchLevel.CANCEL_PENDING_ENTRIES
    assert result.halt.changed is False
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.CANCEL_PENDING_ENTRIES
    assert get_execution_mode(session).mode is ExecutionLifecycleMode.SANDBOX


def test_invalid_downshift_target_still_leaves_fail_closed_halt(session) -> None:
    import app.execution.automatic_safety as safety

    _set_mode(session, ExecutionLifecycleMode.CANARY)

    with pytest.raises(safety.AutomaticSafetyRejected, match="lower-risk"):
        safety.automatic_halt_and_downshift(
            session,
            target=ExecutionLifecycleMode.LIVE,
            reason="misconfigured automatic demotion target",
        )

    # HALT is intentionally durable before target validation/mutation. A bad
    # demotion mapping must fail safe rather than silently leave entries open.
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.HALT_NEW_ENTRIES
    assert get_execution_mode(session).mode is ExecutionLifecycleMode.CANARY


def test_sequence_uses_one_execution_control_lock_and_no_recursive_mode_lock(
    session, monkeypatch
) -> None:
    import app.execution.automatic_safety as safety
    import app.execution.mode as mode_module

    _set_mode(session, ExecutionLifecycleMode.CANARY)
    depth = 0
    acquisitions = 0

    @contextlib.contextmanager
    def one_lock(_db):
        nonlocal depth, acquisitions
        acquisitions += 1
        depth += 1
        assert depth == 1, "automatic safety recursively acquired execution control"
        try:
            yield
        finally:
            depth -= 1

    monkeypatch.setattr(safety, "execution_control_lock", one_lock)
    monkeypatch.setattr(mode_module, "execution_control_lock", one_lock)

    result = safety.automatic_halt_and_downshift(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
        reason="serialized demotion drill",
    )

    assert result.downshift.after is ExecutionLifecycleMode.SANDBOX
    assert acquisitions == 1
    assert depth == 0
