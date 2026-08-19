"""Automatic lower-risk execution safety actions (SAI-033 / B6.4).

Automation in this module is intentionally one-way: it may reduce execution
risk or halt new entries, but it can never promote execution mode or weaken an
existing stronger kill-switch level.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .enums import ExecutionKillSwitchLevel, ExecutionLifecycleMode
from .kill_switch import get_execution_kill_switch_level, set_execution_kill_switch
from .mode import get_execution_mode
from .promotion_guard import authorize_halt_new_entries, change_mode_with_guard


class AutomaticSafetyRejected(ValueError):
    """Raised when an automatic action would not strictly reduce risk."""


@dataclass(frozen=True)
class AutomaticDownshiftResult:
    before: ExecutionLifecycleMode
    after: ExecutionLifecycleMode
    changed: bool


@dataclass(frozen=True)
class AutomaticHaltResult:
    before: ExecutionKillSwitchLevel
    after: ExecutionKillSwitchLevel
    changed: bool


_MODE_RISK_RANK = {
    ExecutionLifecycleMode.PAPER: 0,
    ExecutionLifecycleMode.SANDBOX: 1,
    ExecutionLifecycleMode.CANARY: 2,
    ExecutionLifecycleMode.LIVE: 3,
}

_KILL_SWITCH_RANK = {
    ExecutionKillSwitchLevel.CLEAR: 0,
    ExecutionKillSwitchLevel.HALT_NEW_ENTRIES: 1,
    ExecutionKillSwitchLevel.CANCEL_PENDING_ENTRIES: 2,
    ExecutionKillSwitchLevel.FLATTEN_ALL: 3,
}


def automatic_downshift(
    db: Session,
    *,
    target: ExecutionLifecycleMode,
    reason: str,
) -> AutomaticDownshiftResult:
    """Move execution only to a strictly lower-risk lifecycle mode."""

    reason = reason.strip()
    if not reason:
        raise AutomaticSafetyRejected("automatic downshift reason is required")

    target = ExecutionLifecycleMode(target)
    before = get_execution_mode(db).mode
    if _MODE_RISK_RANK[target] >= _MODE_RISK_RANK[before]:
        raise AutomaticSafetyRejected(
            "automatic mode action must target a strictly lower-risk mode"
        )

    snapshot = change_mode_with_guard(
        db,
        target=target,
        actor="system",
        reason=reason,
    )
    return AutomaticDownshiftResult(
        before=before,
        after=snapshot.mode,
        changed=snapshot.mode != before,
    )


def automatic_halt_new_entries(
    db: Session,
    *,
    reason: str,
) -> AutomaticHaltResult:
    """Apply HALT_NEW_ENTRIES without ever weakening a stronger owner action."""

    reason = reason.strip()
    if not reason:
        raise AutomaticSafetyRejected("automatic halt reason is required")

    # Keep policy ownership in the promotion/safety guard. SAI-033 performs the
    # durable state mutation only after that policy explicitly permits HALT.
    authorization = authorize_halt_new_entries(reason=reason)
    if not authorization.allowed:
        raise AutomaticSafetyRejected("automatic HALT_NEW_ENTRIES was not authorized")

    before = get_execution_kill_switch_level(db)
    if _KILL_SWITCH_RANK[before] >= _KILL_SWITCH_RANK[
        ExecutionKillSwitchLevel.HALT_NEW_ENTRIES
    ]:
        return AutomaticHaltResult(before=before, after=before, changed=False)

    state = set_execution_kill_switch(
        db,
        level=ExecutionKillSwitchLevel.HALT_NEW_ENTRIES,
        actor="system",
        reason=reason,
        audit_action="execution_automatic_halt",
    )
    after = get_execution_kill_switch_level(db)
    if not state.kill_switch or after != ExecutionKillSwitchLevel.HALT_NEW_ENTRIES:
        raise RuntimeError("automatic HALT_NEW_ENTRIES did not persist fail-closed state")
    return AutomaticHaltResult(before=before, after=after, changed=True)


__all__ = [
    "AutomaticDownshiftResult",
    "AutomaticHaltResult",
    "AutomaticSafetyRejected",
    "automatic_downshift",
    "automatic_halt_new_entries",
]
