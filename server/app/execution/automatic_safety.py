"""Automatic lower-risk execution safety actions (SAI-033 / B6.4).

Automation in this module is intentionally one-way: it may reduce execution
risk or halt new entries, but it can never promote execution mode or weaken an
existing stronger kill-switch level.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .enums import ExecutionKillSwitchLevel, ExecutionLifecycleMode
from .kill_switch import (
    _set_execution_kill_switch_locked,
    execution_control_lock,
    get_execution_kill_switch_level,
)
from .mode import _change_execution_mode_locked, get_execution_mode
from .promotion_guard import (
    PromotionEvidence,
    authorize_halt_new_entries,
    change_mode_with_guard,
    evaluate_promotion,
)


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


@dataclass(frozen=True)
class AutomaticHaltDownshiftResult:
    halt: AutomaticHaltResult
    downshift: AutomaticDownshiftResult


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


def _automatic_halt_new_entries_locked(
    db: Session,
    *,
    reason: str,
) -> AutomaticHaltResult:
    """Apply HALT while the caller already holds ``execution_control_lock``."""

    reason = reason.strip()
    if not reason:
        raise AutomaticSafetyRejected("automatic halt reason is required")

    authorization = authorize_halt_new_entries(reason=reason)
    if not authorization.allowed:
        raise AutomaticSafetyRejected("automatic HALT_NEW_ENTRIES was not authorized")

    before = get_execution_kill_switch_level(db)
    if _KILL_SWITCH_RANK[before] >= _KILL_SWITCH_RANK[
        ExecutionKillSwitchLevel.HALT_NEW_ENTRIES
    ]:
        return AutomaticHaltResult(before=before, after=before, changed=False)

    state = _set_execution_kill_switch_locked(
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


def automatic_halt_new_entries(
    db: Session,
    *,
    reason: str,
) -> AutomaticHaltResult:
    """Apply HALT_NEW_ENTRIES without ever weakening a stronger owner action."""

    # Use one execution-control lock for the read/decision/write sequence. The
    # private locked helper is also used by submit-time code that already owns
    # the same dedicated advisory lock and therefore must not acquire it twice.
    with execution_control_lock(db):
        return _automatic_halt_new_entries_locked(db, reason=reason)


def automatic_halt_and_downshift(
    db: Session,
    *,
    target: ExecutionLifecycleMode,
    reason: str,
) -> AutomaticHaltDownshiftResult:
    """Persist HALT first, then apply an explicitly selected lower-risk mode.

    SAI-083 deliberately does not choose the failure-class→mode mapping: that is
    an unresolved ADR-0002 owner decision. This primitive only guarantees the
    safety order once a caller supplies a target. HALT is committed before target
    validation/mutation so a bad mapping or later failure remains fail-closed.
    """

    reason = reason.strip()
    if not reason:
        raise AutomaticSafetyRejected("automatic safety reason is required")

    with execution_control_lock(db):
        before_mode = get_execution_mode(db).mode
        halt = _automatic_halt_new_entries_locked(db, reason=reason)

        try:
            parsed_target = ExecutionLifecycleMode(target)
        except (TypeError, ValueError) as exc:
            raise AutomaticSafetyRejected(
                "automatic mode action must target a strictly lower-risk mode"
            ) from exc

        if _MODE_RISK_RANK[parsed_target] >= _MODE_RISK_RANK[before_mode]:
            raise AutomaticSafetyRejected(
                "automatic mode action must target a strictly lower-risk mode"
            )

        decision = evaluate_promotion(
            current=before_mode,
            target=parsed_target,
            evidence=PromotionEvidence(),
        )
        if not decision.allowed or decision.authorization is None:
            raise AutomaticSafetyRejected(
                "automatic lower-risk mode transition was not authorized"
            )

        try:
            snapshot = _change_execution_mode_locked(
                db,
                target=parsed_target,
                actor="system",
                reason=reason,
                authorization=decision.authorization,
            )
            db.commit()
        except Exception:
            # The HALT helper commits before we reach mode mutation. Rolling
            # back only this later unit of work cannot reopen entries.
            db.rollback()
            raise

        return AutomaticHaltDownshiftResult(
            halt=halt,
            downshift=AutomaticDownshiftResult(
                before=before_mode,
                after=snapshot.mode,
                changed=snapshot.mode != before_mode,
            ),
        )


__all__ = [
    "AutomaticDownshiftResult",
    "AutomaticHaltDownshiftResult",
    "AutomaticHaltResult",
    "AutomaticSafetyRejected",
    "automatic_downshift",
    "automatic_halt_and_downshift",
    "automatic_halt_new_entries",
]
