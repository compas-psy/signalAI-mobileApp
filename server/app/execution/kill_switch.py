"""Durable execution kill-switch control for SAI-028 / B5.5.

The switch is persisted in ``risk_state`` so a process restart cannot silently
resume entries. The legacy boolean remains synchronized for existing health and
mobile consumers, while ``kill_switch_level`` carries the exact action.

This slice deliberately does not issue provider cancel/flatten commands. Venue
capabilities belong to SAI-036. Until then, stronger levels can cancel only
local pre-submit intents; in-flight reconciliation and protection must continue.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from ..models.enums import ExecutionMode
from ..models.risk import AuditEvent, RiskState
from .enums import ExecutionKillSwitchLevel


_EXECUTION_CONTROL_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"signalai-execution-control").digest()[:8],
    byteorder="big",
    signed=True,
)


class ExecutionKillSwitchError(ValueError):
    """Raised when a money-sensitive kill-switch action is not deliberate."""


def _engine_for_session(db: Session) -> Engine:
    """Return the Engine behind either an Engine- or Connection-bound Session."""

    bind = db.get_bind()
    if isinstance(bind, Engine):
        return bind
    if isinstance(bind, Connection):
        return bind.engine
    raise RuntimeError("execution control requires a SQLAlchemy Engine/Connection")


@contextmanager
def execution_control_lock(db: Session) -> Iterator[None]:
    """Serialize kill-switch commits with the actual provider submit call.

    The advisory lock lives on a dedicated PostgreSQL connection, not on the
    ORM Session connection. That is essential: SAI-027 intentionally commits
    the durable ``SUBMITTING`` order before network I/O, and an ordinary
    transaction/row lock would be released at exactly that crash-safety
    boundary. The dedicated session-level advisory lock survives those ORM
    commits and is explicitly released when the guarded operation is complete.
    """

    engine = _engine_for_session(db)
    with engine.connect() as lock_connection:
        lock_connection.execute(
            text("SELECT pg_advisory_lock(:key)"),
            {"key": _EXECUTION_CONTROL_LOCK_KEY},
        ).scalar_one()
        try:
            yield
        finally:
            released = bool(
                lock_connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": _EXECUTION_CONTROL_LOCK_KEY},
                ).scalar_one()
            )
            if not released:
                raise RuntimeError("execution control advisory lock was not held")


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
    # Materialize the singleton before a worker claim. The provider-submit
    # guard performs a fresh authoritative read under execution_control_lock.
    return effective_execution_kill_switch_level(_state(db))


def _snapshot(state: RiskState) -> dict[str, object]:
    return {
        "active": bool(state.kill_switch),
        "level": effective_execution_kill_switch_level(state).value,
        "reason": state.kill_switch_reason,
    }


def _set_execution_kill_switch_locked(
    db: Session,
    *,
    level: ExecutionKillSwitchLevel,
    actor: str,
    reason: str,
    confirm_flatten_all: bool = False,
    audit_action: str = "execution_kill_switch_set",
) -> RiskState:
    """Persist an active level while the caller already holds execution control.

    This private helper is the only mutation path used inside an existing
    ``execution_control_lock`` scope. Acquiring the public lock again from a
    second dedicated PostgreSQL connection would deadlock, so callers must keep
    this function private to already-serialized execution code.
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
    state.kill_switch_level = level.value
    state.kill_switch_reason = reason
    state.updated_at = datetime.now(UTC)
    after = {"active": True, "level": level.value, "reason": reason}
    db.add(
        AuditEvent(
            actor=actor,
            action=audit_action,
            subject="risk_state",
            detail=reason,
            before_json=before,
            after_json=after,
        )
    )
    db.flush()
    db.commit()
    return state


def set_execution_kill_switch(
    db: Session,
    *,
    level: ExecutionKillSwitchLevel,
    actor: str,
    reason: str,
    confirm_flatten_all: bool = False,
    audit_action: str = "execution_kill_switch_set",
) -> RiskState:
    """Persist one active kill-switch level and append its audit fact.

    The state and audit event are committed *while* the execution-control lock
    is held. Therefore a provider submit that starts after this function
    returns must observe the new level before it can touch the venue.

    ``FLATTEN_ALL`` is intentionally harder to request than the other levels.
    It changes the desired handling of open risk and therefore needs an
    explicit second signal from the caller. This service still does not claim
    provider flattening before SAI-036 supplies that capability.

    ``audit_action`` exists only so the old ``/risk/halt`` endpoint can retain
    its established audit vocabulary while the new exact-level API uses the
    SAI-028 action name.
    """

    with execution_control_lock(db):
        return _set_execution_kill_switch_locked(
            db,
            level=level,
            actor=actor,
            reason=reason,
            confirm_flatten_all=confirm_flatten_all,
            audit_action=audit_action,
        )


def clear_execution_kill_switch(
    db: Session,
    *,
    actor: str,
    reason: str = "",
    audit_action: str = "execution_kill_switch_clear",
) -> RiskState:
    """Explicitly restore entry eligibility; never called automatically here."""

    with execution_control_lock(db):
        state = _state(db)
        before = _snapshot(state)
        state.kill_switch = False
        state.kill_switch_level = ExecutionKillSwitchLevel.CLEAR.value
        state.kill_switch_reason = ""
        state.updated_at = datetime.now(UTC)
        db.add(
            AuditEvent(
                actor=actor,
                action=audit_action,
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
        db.commit()
        return state


__all__ = [
    "ExecutionKillSwitchError",
    "clear_execution_kill_switch",
    "effective_execution_kill_switch_level",
    "execution_control_lock",
    "get_execution_kill_switch_level",
    "set_execution_kill_switch",
]
