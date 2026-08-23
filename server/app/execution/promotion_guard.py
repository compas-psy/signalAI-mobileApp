"""ADR-backed execution-mode promotion guard (SAI-031 / B6.2).

The guard deliberately evaluates categories of proof rather than inventing
numeric thresholds that are not present in the approved backlog. Production
proof remains fail-closed until later venue/owner/performance slices provide
real evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy.orm import Session

from .enums import ExecutionLifecycleMode
from .mode import (
    ExecutionModeChangeRejected,
    ExecutionModeSnapshot,
    ModeChangeAuthorization,
    change_execution_mode,
    get_execution_mode,
)
from .promotion_evidence import (
    PromotionEvidenceScope,
    current_persisted_promotion_evidence,
    record_promotion_evidence_decision,
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
    snapshot_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromotionDecision:
    current: ExecutionLifecycleMode
    target: ExecutionLifecycleMode
    allowed: bool
    blockers: tuple[str, ...]
    evidence_notes: tuple[str, ...]
    authorization: ModeChangeAuthorization | None
    correlation_id: str | None = None
    evidence_snapshot_ids: tuple[str, ...] = ()


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
            evidence_snapshot_ids=evidence.snapshot_ids,
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
            evidence_snapshot_ids=evidence.snapshot_ids,
        )

    if target_rank != current_rank + 1:
        return PromotionDecision(
            current=current,
            target=target,
            allowed=False,
            blockers=("stepwise promotion required",),
            evidence_notes=evidence.notes,
            authorization=None,
            evidence_snapshot_ids=evidence.snapshot_ids,
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
        if not evidence.adr_gates_passed:
            blockers.append("ADR gates not verified")
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
            evidence_snapshot_ids=evidence.snapshot_ids,
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
        evidence_snapshot_ids=evidence.snapshot_ids,
    )


def current_server_promotion_evidence(
    db: Session,
    *,
    current: ExecutionLifecycleMode,
    target: ExecutionLifecycleMode,
    scope: PromotionEvidenceScope | None = None,
    now: datetime | None = None,
) -> PromotionEvidence:
    """Build only evidence the server can prove today.

    PAPER -> SANDBOX has one global provider-owned acceptance proof: a real
    T-Invest Sandbox LIMIT BUY/SELL round trip bound to the exact deployed SHA
    and current sandbox credential generation. Later risk-increasing stages
    remain strategy-scoped and fail closed without their authoritative scope.
    """

    if (
        scope is None
        and current == ExecutionLifecycleMode.PAPER
        and target == ExecutionLifecycleMode.SANDBOX
    ):
        from .venues.tinvest_sandbox_readiness import current_tinvest_sandbox_readiness

        readiness = current_tinvest_sandbox_readiness(db)
        return PromotionEvidence(
            technical_sandbox_ready=readiness.ready,
            notes=readiness.notes,
        )

    if scope is not None:
        return current_persisted_promotion_evidence(
            db, scope=scope, target=target, now=now
        )
    return PromotionEvidence(
        notes=(
            "promotion evidence scope is required",
            "venue sandbox capability not verified",
        )
    )


def preview_promotion(
    db: Session,
    *,
    target: ExecutionLifecycleMode,
    scope: PromotionEvidenceScope | None = None,
    now: datetime | None = None,
) -> PromotionDecision:
    current = get_execution_mode(db).mode
    evidence = current_server_promotion_evidence(
        db,
        current=current,
        target=target,
        scope=scope,
        now=now,
    )
    decision = evaluate_promotion(current=current, target=target, evidence=evidence)
    decision = replace(decision, evidence_snapshot_ids=evidence.snapshot_ids)
    correlation_id = record_promotion_evidence_decision(
        db, decision=decision, scope=scope
    )
    authorization = decision.authorization
    if authorization is not None:
        authorization = replace(
            authorization,
            detail_json={
                **authorization.detail_json,
                "promotion_evidence_correlation_id": correlation_id,
                "promotion_evidence_snapshot_ids": list(evidence.snapshot_ids),
            },
        )
    return replace(
        decision, correlation_id=correlation_id, authorization=authorization
    )


def change_mode_with_guard(
    db: Session,
    *,
    target: ExecutionLifecycleMode,
    actor: str,
    reason: str,
    scope: PromotionEvidenceScope | None = None,
) -> ExecutionModeSnapshot:
    decision = preview_promotion(db, target=target, scope=scope)
    if not decision.allowed:
        raise ExecutionModeChangeRejected(decision.blockers)
    if decision.current == decision.target:
        return get_execution_mode(db)
    authorization = decision.authorization
    if authorization is None:
        raise ExecutionModeChangeRejected(("promotion authorization missing",))
    return change_execution_mode(
        db,
        target=target,
        actor=actor,
        reason=reason,
        authorization=authorization,
    )


def authorize_halt_new_entries(*, reason: str) -> HaltAuthorization:
    authorization = ModeChangeAuthorization(
        allowed=True,
        actor="promotion-guard",
        reason=reason,
        detail_json={
            "policy_version": POLICY_VERSION,
            "direction": "risk-reduction",
            "action": "HALT_NEW_ENTRIES",
        },
    )
    return HaltAuthorization(
        allowed=True,
        action="HALT_NEW_ENTRIES",
        authorization=authorization,
    )
