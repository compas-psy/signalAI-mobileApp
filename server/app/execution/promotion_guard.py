"""ADR-backed execution-mode promotion guard (SAI-031 / B6.2).

The guard deliberately evaluates categories of proof rather than inventing
numeric thresholds that are not present in the approved backlog. Production
proof remains fail-closed until later venue/owner/performance slices provide
real evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .enums import ExecutionLifecycleMode
from .mode import (
    ExecutionModeChangeRejected,
    ExecutionModeSnapshot,
    ModeChangeAuthorization,
    change_execution_mode,
    get_execution_mode,
)


POLICY_VERSION = "ADR-0001"


@dataclass(frozen=True)
class PromotionEvidence:
    technical_sandbox_ready: bool = False
    adr_gates_passed: bool = False
    owner_confirmed: bool = False
    performance_gates_passed: bool = False
    ops_gates_passed: bool = False
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromotionDecision:
    current: ExecutionLifecycleMode
    target: ExecutionLifecycleMode
    allowed: bool
    blockers: tuple[str, ...]
    evidence_notes: tuple[str, ...]
    authorization: ModeChangeAuthorization | None


@dataclass(frozen=True)
class HaltAuthorization:
    allowed: bool
    action: str
    authorization: ModeChangeAuthorization


_RISK_RANK = {
    ExecutionLifecycleMode.PAPER: 0,
    ExecutionLifecycleMode.SANDBOX: 1,
    ExecutionLifecycleMode.CANARY: 2,
    ExecutionLifecycleMode.LIVE: 3,
}


def _authorization(
    *,
    current: ExecutionLifecycleMode,
    target: ExecutionLifecycleMode,
    reason: str,
    direction: str,
) -> ModeChangeAuthorization:
    return ModeChangeAuthorization(
        allowed=True,
        actor="promotion-guard",
        reason=reason,
        detail_json={
            "policy_version": POLICY_VERSION,
            "from_mode": current.value,
            "to_mode": target.value,
            "direction": direction,
        },
    )


def evaluate_promotion(
    *,
    current: ExecutionLifecycleMode,
    target: ExecutionLifecycleMode,
    evidence: PromotionEvidence,
) -> PromotionDecision:
    """Evaluate one mode transition against the approved B6.2 categories."""

    current = ExecutionLifecycleMode(current)
    target = ExecutionLifecycleMode(target)

    if current == target:
        return PromotionDecision(
            current=current,
            target=target,
            allowed=True,
            blockers=(),
            evidence_notes=evidence.notes,
            authorization=None,
        )

    current_rank = _RISK_RANK[current]
    target_rank = _RISK_RANK[target]

    if target_rank < current_rank:
        return PromotionDecision(
            current=current,
            target=target,
            allowed=True,
            blockers=(),
            evidence_notes=evidence.notes,
            authorization=_authorization(
                current=current,
                target=target,
                reason="lower-risk mode transition is automatically permitted",
                direction="lower-risk",
            ),
        )

    if target_rank != current_rank + 1:
        return PromotionDecision(
            current=current,
            target=target,
            allowed=False,
            blockers=("stepwise promotion required",),
            evidence_notes=evidence.notes,
            authorization=None,
        )

    blockers: list[str] = []
    if current == ExecutionLifecycleMode.PAPER and target == ExecutionLifecycleMode.SANDBOX:
        if not evidence.technical_sandbox_ready:
            blockers.append("technical sandbox readiness not verified")
    elif current == ExecutionLifecycleMode.SANDBOX and target == ExecutionLifecycleMode.CANARY:
        if not evidence.adr_gates_passed:
            blockers.append("ADR gates not verified")
        if not evidence.owner_confirmed:
            blockers.append("explicit owner confirmation missing")
    elif current == ExecutionLifecycleMode.CANARY and target == ExecutionLifecycleMode.LIVE:
        if not evidence.owner_confirmed:
            blockers.append("explicit owner confirmation missing")
        if not evidence.performance_gates_passed:
            blockers.append("performance gates not verified")
        if not evidence.ops_gates_passed:
            blockers.append("ops gates not verified")

    if blockers:
        return PromotionDecision(
            current=current,
            target=target,
            allowed=False,
            blockers=tuple(blockers),
            evidence_notes=evidence.notes,
            authorization=None,
        )

    return PromotionDecision(
        current=current,
        target=target,
        allowed=True,
        blockers=(),
        evidence_notes=evidence.notes,
        authorization=_authorization(
            current=current,
            target=target,
            reason=f"{POLICY_VERSION} promotion evidence verified",
            direction="promotion",
        ),
    )


def current_server_promotion_evidence(
    db: Session,
    *,
    current: ExecutionLifecycleMode,
    target: ExecutionLifecycleMode,
) -> PromotionEvidence:
    """Build only evidence the server can prove today.

    Later slices own the real providers for these gates. In SAI-031 there is no
    VenueAdapter capability proof (SAI-036), no two-step owner activation proof
    (SAI-032), and no approved numeric performance/ops promotion policy. The
    correct production answer is therefore explicit missing evidence, not a
    guessed green state.
    """

    del db, current, target
    return PromotionEvidence(
        notes=(
            "venue sandbox capability not verified",
            "owner activation proof not provided",
            "performance promotion evidence not wired",
            "ops promotion evidence not wired",
        )
    )


def preview_promotion(
    db: Session,
    *,
    target: ExecutionLifecycleMode,
) -> PromotionDecision:
    current = get_execution_mode(db).mode
    evidence = current_server_promotion_evidence(
        db,
        current=current,
        target=target,
    )
    return evaluate_promotion(current=current, target=target, evidence=evidence)


def change_mode_with_guard(
    db: Session,
    *,
    target: ExecutionLifecycleMode,
    actor: str,
    reason: str,
) -> ExecutionModeSnapshot:
    """Apply only a transition currently authorized by the server guard."""

    decision = preview_promotion(db, target=target)
    if not decision.allowed:
        raise ExecutionModeChangeRejected("; ".join(decision.blockers))

    return change_execution_mode(
        db,
        target=target,
        actor=actor,
        reason=reason,
        authorization=decision.authorization,
    )


def authorize_halt_new_entries(*, reason: str) -> HaltAuthorization:
    """Authorize the always-safe direction toward HALT without mutating state.

    SAI-033 owns automatic trigger logic and invokes the SAI-028 kill-switch
    service. Keeping this function pure prevents mode and kill-switch state from
    becoming two competing sources of truth.
    """

    reason = reason.strip()
    if not reason:
        raise ExecutionModeChangeRejected("halt reason is required")
    authorization = ModeChangeAuthorization(
        allowed=True,
        actor="promotion-guard",
        reason=reason,
        detail_json={
            "policy_version": POLICY_VERSION,
            "direction": "lower-risk",
            "action": "HALT_NEW_ENTRIES",
        },
    )
    return HaltAuthorization(
        allowed=True,
        action="HALT_NEW_ENTRIES",
        authorization=authorization,
    )


__all__ = [
    "HaltAuthorization",
    "POLICY_VERSION",
    "PromotionDecision",
    "PromotionEvidence",
    "authorize_halt_new_entries",
    "change_mode_with_guard",
    "current_server_promotion_evidence",
    "evaluate_promotion",
    "preview_promotion",
]
