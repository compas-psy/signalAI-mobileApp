"""Server-owned execution lifecycle mode (SAI-030 / B6.1).

This module owns the persisted PAPER/SANDBOX/CANARY/LIVE mode and its
append-only change trail. It deliberately does *not* implement promotion
policy: SAI-031 plugs the ADR-backed guard into ``ModeChangeAuthorization``.
Until then every risk-increasing mode change is fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..models.execution import ExecutionModeEvent, ExecutionModeState
from .enums import ExecutionLifecycleMode
from .kill_switch import execution_control_lock


class ExecutionModeChangeRejected(ValueError):
    """Raised when no authoritative promotion guard permits a mode change."""


@dataclass(frozen=True)
class ExecutionModeSnapshot:
    mode: ExecutionLifecycleMode
    updated_at: datetime


@dataclass(frozen=True)
class ExecutionModePreview:
    current: ExecutionLifecycleMode
    target: ExecutionLifecycleMode
    allowed: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class ModeChangeAuthorization:
    """Internal proof supplied by the promotion guard implemented in SAI-031."""

    allowed: bool
    actor: str
    reason: str
    detail_json: dict[str, object] = field(default_factory=dict)


_PROMOTION_GUARD_BLOCKER = (
    "promotion guard is not installed; SAI-031 must authorize mode changes"
)


def _materialize_state(db: Session) -> ExecutionModeState:
    """Create the singleton safely under concurrent first reads."""

    db.execute(
        insert(ExecutionModeState)
        .values(id=1, mode=ExecutionLifecycleMode.PAPER.value)
        .on_conflict_do_nothing(index_elements=[ExecutionModeState.id])
    )
    db.flush()
    state = db.get(ExecutionModeState, 1, populate_existing=True)
    if state is None:  # Defensive: the singleton insert/select must resolve.
        raise RuntimeError("execution mode singleton could not be materialized")
    return state


def _snapshot(state: ExecutionModeState) -> ExecutionModeSnapshot:
    mode = ExecutionLifecycleMode(state.mode)
    return ExecutionModeSnapshot(mode=mode, updated_at=state.updated_at)


def get_execution_mode(db: Session) -> ExecutionModeSnapshot:
    """Read the authoritative server-side execution mode from PostgreSQL."""

    return _snapshot(_materialize_state(db))


def preview_execution_mode(
    db: Session,
    *,
    target: ExecutionLifecycleMode,
) -> ExecutionModePreview:
    """Preview a mode request without inventing promotion readiness.

    SAI-030 only establishes ownership and API shape. Until SAI-031 evaluates
    real technical/ADR/performance gates, a change to another mode cannot be
    declared safe. Same-mode requests are harmless and therefore idempotently
    allowed.
    """

    target = ExecutionLifecycleMode(target)
    current = get_execution_mode(db).mode
    if target == current:
        return ExecutionModePreview(
            current=current,
            target=target,
            allowed=True,
            blockers=(),
        )
    return ExecutionModePreview(
        current=current,
        target=target,
        allowed=False,
        blockers=(_PROMOTION_GUARD_BLOCKER,),
    )


def change_execution_mode(
    db: Session,
    *,
    target: ExecutionLifecycleMode,
    actor: str,
    reason: str,
    authorization: ModeChangeAuthorization | None = None,
) -> ExecutionModeSnapshot:
    """Apply one explicitly authorized lifecycle-mode change.

    Public SAI-030 API callers do not possess an authorization and therefore
    remain fail-closed. SAI-031 will be the only component allowed to mint this
    proof after rechecking its gates. The append-only event preserves both the
    owner request and the guard evidence used to permit it.

    Mode mutation shares the session-level execution-control lock with the
    Canary submit guard. Unlike transaction row locks, this advisory lock
    survives the crash-safety commit that a future provider adapter performs
    after persisting SUBMITTING, so mode cannot drift inside that write window.
    """

    target = ExecutionLifecycleMode(target)
    actor = actor.strip()
    reason = reason.strip()
    if not actor:
        raise ExecutionModeChangeRejected("actor is required")
    if not reason:
        raise ExecutionModeChangeRejected("reason is required")

    with execution_control_lock(db):
        _materialize_state(db)
        state = db.execute(
            select(ExecutionModeState)
            .where(ExecutionModeState.id == 1)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        current = ExecutionLifecycleMode(state.mode)

        if target == current:
            return _snapshot(state)

        if authorization is None or not authorization.allowed:
            raise ExecutionModeChangeRejected(_PROMOTION_GUARD_BLOCKER)
        if not authorization.actor.strip() or not authorization.reason.strip():
            raise ExecutionModeChangeRejected(
                "promotion guard authorization actor and reason are required"
            )

        state.mode = target
        state.updated_at = datetime.now(UTC)
        detail = dict(authorization.detail_json)
        detail.update(
            {
                "authorization_actor": authorization.actor.strip(),
                "authorization_reason": authorization.reason.strip(),
            }
        )
        db.add(
            ExecutionModeEvent(
                from_mode=current,
                to_mode=target,
                actor=actor,
                reason=reason,
                detail_json=detail,
            )
        )
        db.flush()
        return _snapshot(state)


__all__ = [
    "ExecutionModeChangeRejected",
    "ExecutionModePreview",
    "ExecutionModeSnapshot",
    "ModeChangeAuthorization",
    "change_execution_mode",
    "get_execution_mode",
    "preview_execution_mode",
]
