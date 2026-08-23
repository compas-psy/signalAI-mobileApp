"""Automatic lower-risk execution safety actions (SAI-033 / B6.4).

Automation in this module is intentionally one-way: it may reduce execution
risk or halt new entries, but it can never promote execution mode or weaken an
existing stronger kill-switch level.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_config
from ..models.execution import ExecutionFill, ExecutionIntent, ExecutionProtection
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


@dataclass(frozen=True)
class CanaryProtectionSafetyResult:
    trigger: str | None
    naked_ms: int | None
    protection_sla_ms: int
    halt: AutomaticHaltResult | None


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


def automatic_halt_if_canary_missing_protection(
    db: Session,
    *,
    intent_id: uuid.UUID,
    as_of: datetime | None = None,
) -> CanaryProtectionSafetyResult:
    """Halt Canary entries when a filled Lighter intent is currently unprotected.

    This guard reuses the versioned execution protection SLA. It deliberately
    evaluates current durable protection state rather than a historical timing
    violation: once a provider-side stop is ACTIVE, owner recovery must not be
    trapped by an old late-arm event. Provider freshness/reconciliation policy
    remains a separate owner-approved concern and is not invented here.
    """

    as_of = as_of or datetime.now(UTC)
    protection_sla_ms = int(get_config().get("execution.protection_sla_seconds")) * 1000

    with execution_control_lock(db):
        intent = db.get(ExecutionIntent, intent_id, populate_existing=True)
        if intent is None:
            raise LookupError(f"execution intent {intent_id} does not exist")

        runtime_mode = get_execution_mode(db).mode
        if (
            runtime_mode is not ExecutionLifecycleMode.CANARY
            or intent.execution_mode_snapshot is not ExecutionLifecycleMode.CANARY
            or intent.venue.upper() != "LIGHTER"
        ):
            return CanaryProtectionSafetyResult(
                trigger=None,
                naked_ms=None,
                protection_sla_ms=protection_sla_ms,
                halt=None,
            )

        first_fill = db.execute(
            select(ExecutionFill)
            .where(ExecutionFill.intent_id == intent.id)
            .order_by(ExecutionFill.filled_at, ExecutionFill.id)
            .limit(1)
        ).scalar_one_or_none()
        if first_fill is None:
            return CanaryProtectionSafetyResult(
                trigger=None,
                naked_ms=None,
                protection_sla_ms=protection_sla_ms,
                halt=None,
            )

        active_protections = db.execute(
            select(ExecutionProtection).where(
                ExecutionProtection.intent_id == intent.id,
                ExecutionProtection.status == "ACTIVE",
                ExecutionProtection.armed_at.is_not(None),
            )
        ).scalars()
        if any(
            bool(str(protection.provider_order_id or "").strip())
            for protection in active_protections
        ):
            return CanaryProtectionSafetyResult(
                trigger=None,
                naked_ms=None,
                protection_sla_ms=protection_sla_ms,
                halt=None,
            )

        naked_ms = int(round((as_of - first_fill.filled_at).total_seconds() * 1000))
        if naked_ms <= protection_sla_ms:
            return CanaryProtectionSafetyResult(
                trigger=None,
                naked_ms=naked_ms,
                protection_sla_ms=protection_sla_ms,
                halt=None,
            )

        halt = _automatic_halt_new_entries_locked(
            db,
            reason=(
                f"Canary Lighter intent {intent.id} has no active provider-side "
                f"protection after {naked_ms} ms (SLA {protection_sla_ms} ms)"
            ),
        )
        return CanaryProtectionSafetyResult(
            trigger="MISSING_PROTECTION",
            naked_ms=naked_ms,
            protection_sla_ms=protection_sla_ms,
            halt=halt,
        )


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
    "CanaryProtectionSafetyResult",
    "automatic_downshift",
    "automatic_halt_and_downshift",
    "automatic_halt_if_canary_missing_protection",
    "automatic_halt_new_entries",
]
