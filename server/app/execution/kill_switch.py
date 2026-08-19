"""Durable execution kill-switch control for SAI-028 / B5.5.

The switch is persisted in ``risk_state`` so a process restart cannot silently
resume entries. The legacy boolean remains synchronized for existing health and
mobile consumers, while ``kill_switch_level`` carries the exact action.

This slice deliberately does not issue provider cancel/flatten commands. Venue
capabilities belong to SAI-036. Until then, stronger levels can cancel only
local pre-submit intents; in-flight reconciliation and protection must continue.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..models.enums import ExecutionMode
from ..models.risk import AuditEvent, RiskState
from .enums import ExecutionKillSwitchLevel


class ExecutionKillSwitchError(ValueError):
    """Raised when a money-sensitive kill-switch action is not deliberate."""


def _state(db: Session) -> RiskState:
    state = db.get(RiskState, 1)
    if state is None:
        state = RiskState(
            id=1,
            execution_mode=ExecutionMode.PAPER,
            kill_switch=False,
            kill_switch_level=ExecutionKillSwitchLevel.CLEAR,
            kill_switch_reason="",
        )
        db.add(state)
        db.flush()
    return state


def effective_execution_kill_switch_level(
    state: RiskState | None,
) -> ExecutionKillSwitchLevel:
    """Return the exact level, honoring a pre-SAI-028 boolean halt.

    The fallback is intentional. A row written by an older process with
    ``kill_switch=true`` and no meaningful level must still fail closed as
    ``HALT_NEW_ENTRIES`` rather than being treated as clear.
    """

    if state is None:
        return ExecutionKillSwitchLevel.CLEAR

    raw = getattr(state, "kill_switch_level", ExecutionKillSwitchLevel.CLEAR)
    try:
        level = ExecutionKillSwitchLevel(raw)
    except (TypeError, ValueError):
        level = ExecutionKillSwitchLevel.HALT_NEW_ENTRIES

    if level == ExecutionKillSwitchLevel.CLEAR and bool(state.kill_switch):
        return ExecutionKillSwitchLevel.HALT_NEW_ENTRIES
    return level


def get_execution_kill_switch_level(db: Session) -> ExecutionKillSwitchLevel:
    # Materialize the singleton before a worker claim so the later SELECT FOR
    # UPDATE always has a row to serialize against a concurrent owner action.
    return effective_execution_kill_switch_level(_state(db))


def _snapshot(state: RiskState) -> dict[str, object]:
    return {
        "active": bool(state.kill_switch),
        "level": effective_execution_kill_switch_level(state).value,
        "reason": state.kill_switch_reason,
    }


def set_execution_kill_switch(
    db: Session,
    *,
    level: ExecutionKillSwitchLevel,
    actor: str,
    reason: str,
    confirm_flatten_all: bool = False,
) -> RiskState:
    """Persist one active kill-switch level and append its audit fact.

    ``FLATTEN_ALL`` is intentionally harder to request than the other levels.
    It changes the desired handling of open risk and therefore needs an
    explicit second signal from the caller. This service still does not claim
    provider flattening before SAI-036 supplies that capability.
    """

    try:
        level = ExecutionKillSwitchLevel(level)
    except ValueError as exc:
        raise ExecutionKillSwitchError("unknown execution kill-switch level") from exc

    if level == ExecutionKillSwitchLevel.CLEAR:
        raise ExecutionKillSwitchError("use clear_execution_kill_switch to resume")

    reason = reason.strip()
    if not reason:
        raise ExecutionKillSwitchError("kill-switch reason is required")
    if level == ExecutionKillSwitchLevel.FLATTEN_ALL and not confirm_flatten_all:
        raise ExecutionKillSwitchError(
            "FLATTEN_ALL requires explicit confirm_flatten_all=true"
        )

    state = _state(db)
    before = _snapshot(state)
    state.kill_switch = True
    state.kill_switch_level = level
    state.kill_switch_reason = reason
    state.updated_at = datetime.now(UTC)
    after = {"active": True, "level": level.value, "reason": reason}
    db.add(
        AuditEvent(
            actor=actor,
            action="execution_kill_switch_set",
            subject="risk_state",
            detail=reason,
            before_json=before,
            after_json=after,
        )
    )
    db.flush()
    return state


def clear_execution_kill_switch(
    db: Session,
    *,
    actor: str,
    reason: str = "",
) -> RiskState:
    """Explicitly restore entry eligibility; never called automatically here."""

    state = _state(db)
    before = _snapshot(state)
    state.kill_switch = False
    state.kill_switch_level = ExecutionKillSwitchLevel.CLEAR
    state.kill_switch_reason = ""
    state.updated_at = datetime.now(UTC)
    db.add(
        AuditEvent(
            actor=actor,
            action="execution_kill_switch_clear",
            subject="risk_state",
            detail=reason.strip(),
            before_json=before,
            after_json={
                "active": False,
                "level": ExecutionKillSwitchLevel.CLEAR.value,
                "reason": "",
            },
        )
    )
    db.flush()
    return state


__all__ = [
    "ExecutionKillSwitchError",
    "clear_execution_kill_switch",
    "effective_execution_kill_switch_level",
    "get_execution_kill_switch_level",
    "set_execution_kill_switch",
]
