"""Read-only factual proof report for observed Lighter Canary execution.

The report deliberately does not decide whether Canary is good enough to scale.
Acceptance thresholds remain unresolved.  Execution facts are counted only
inside one fully owner-bound immutable Canary policy session; unbound/legacy
CANARY history is never accepted as proof.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.execution import ExecutionFill, ExecutionModeEvent, ExecutionProtection
from .canary_correlation import (
    CanaryCorrelationError,
    CanaryExecutionScope,
    resolve_canary_execution_scope,
)
from .enums import ExecutionLifecycleMode
from .health import execution_health_for_intent

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ACCEPTANCE_POLICY_BLOCKER = "CANARY_PROOF_ACCEPTANCE_THRESHOLDS_NOT_APPROVED"
_ACTIVATION_BINDING_BLOCKER = "CANARY_ACTIVATION_POLICY_BINDING_MISSING"


@dataclass(frozen=True, slots=True)
class CanaryProofReport:
    status: str
    observed_at: datetime
    policy_snapshot_hash: str | None
    canary_intent_count: int
    filled_intent_count: int
    fill_count: int
    fees_by_currency: dict[str, Decimal]
    average_fill_deviation_bps: Decimal | None
    worst_fill_deviation_bps: Decimal | None
    protection_slo_breach_count: int
    current_unprotected_filled_intent_count: int
    reconciliation_mismatch_count: int
    rejected_order_count: int
    duplicate_prevention_count: int
    acceptance_ready: bool
    blockers: tuple[str, ...]


def _current_active_protection_exists(db: Session, *, intent_id) -> bool:
    rows = db.execute(
        select(ExecutionProtection).where(
            ExecutionProtection.intent_id == intent_id,
            ExecutionProtection.status == "ACTIVE",
            ExecutionProtection.armed_at.is_not(None),
        )
    ).scalars()
    return any(bool(str(item.provider_order_id or "").strip()) for item in rows)


def _latest_canary_policy_candidate(db: Session) -> str | None:
    """Read only the newest CANARY transition; never fall back to stale proof."""

    event = db.execute(
        select(ExecutionModeEvent)
        .where(ExecutionModeEvent.to_mode == ExecutionLifecycleMode.CANARY)
        .order_by(ExecutionModeEvent.occurred_at.desc(), ExecutionModeEvent.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if event is None:
        return None
    detail = event.detail_json if isinstance(event.detail_json, dict) else {}
    candidate = str(detail.get("canary_policy_snapshot_hash") or "").strip().lower()
    return candidate if _HEX64.fullmatch(candidate) is not None else None


def _resolved_scope(
    db: Session,
    *,
    snapshot_hash: str | None,
) -> CanaryExecutionScope | None:
    requested = snapshot_hash
    explicit = requested is not None
    if requested is None:
        requested = _latest_canary_policy_candidate(db)
    if requested is None:
        return None

    try:
        scope = resolve_canary_execution_scope(db, snapshot_hash=requested)
    except CanaryCorrelationError:
        if explicit:
            raise
        return None
    return scope if scope.fully_bound else None


def build_canary_proof_report(
    db: Session,
    *,
    snapshot_hash: str | None = None,
    as_of: datetime | None = None,
) -> CanaryProofReport:
    """Aggregate durable execution facts from exactly one bound Canary session.

    PAPER, SANDBOX, LIVE, non-Lighter execution, other accounts/strategies,
    instruments outside the immutable allowlist, and execution outside the
    bound CANARY window are intentionally excluded.  The function performs no
    writes and never converts observed data into a promotion decision while
    owner acceptance criteria are unresolved.
    """

    observed_at = as_of or datetime.now(UTC)
    scope = _resolved_scope(db, snapshot_hash=snapshot_hash)
    intents = () if scope is None else scope.intents
    policy_snapshot_hash = None if scope is None else scope.snapshot.snapshot_hash

    fees: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    deviations: list[Decimal] = []
    filled_intent_count = 0
    fill_count = 0
    protection_slo_breach_count = 0
    current_unprotected_filled_intent_count = 0
    reconciliation_mismatch_count = 0
    rejected_order_count = 0
    duplicate_prevention_count = 0

    for intent in intents:
        fills = tuple(
            db.execute(
                select(ExecutionFill)
                .where(ExecutionFill.intent_id == intent.id)
                .order_by(ExecutionFill.filled_at, ExecutionFill.id)
            ).scalars()
        )
        if fills:
            filled_intent_count += 1
            fill_count += len(fills)
            for fill in fills:
                currency = str(fill.fee_currency or "UNKNOWN").strip() or "UNKNOWN"
                fees[currency] += Decimal(fill.fee_amount)
            if not _current_active_protection_exists(db, intent_id=intent.id):
                current_unprotected_filled_intent_count += 1

        health = execution_health_for_intent(
            db,
            intent_id=intent.id,
            as_of=observed_at,
        )
        if health.fill_deviation_bps is not None:
            deviations.append(Decimal(health.fill_deviation_bps))
        if any(v.code == "PROTECTION_ARM_SLO" for v in health.violations):
            protection_slo_breach_count += 1
        reconciliation_mismatch_count += health.reconciliation_mismatch_count
        rejected_order_count += health.rejected_order_count
        duplicate_prevention_count += health.duplicate_prevention_count

    blockers: list[str] = []
    if scope is None:
        blockers.append(_ACTIVATION_BINDING_BLOCKER)
    if not intents:
        blockers.append("NO_CANARY_EXECUTION_EVIDENCE")
    elif filled_intent_count == 0:
        blockers.append("NO_CANARY_FILL_EVIDENCE")
    blockers.append(_ACCEPTANCE_POLICY_BLOCKER)

    status = (
        "OBSERVED"
        if scope is not None and intents and filled_intent_count > 0
        else "INSUFFICIENT_EVIDENCE"
    )

    average_deviation = None
    worst_deviation = None
    if deviations:
        average_deviation = (
            sum(deviations, Decimal("0")) / Decimal(len(deviations))
        ).quantize(Decimal("0.01"))
        worst_deviation = max(deviations).quantize(Decimal("0.01"))

    return CanaryProofReport(
        status=status,
        observed_at=observed_at,
        policy_snapshot_hash=policy_snapshot_hash,
        canary_intent_count=len(intents),
        filled_intent_count=filled_intent_count,
        fill_count=fill_count,
        fees_by_currency=dict(fees),
        average_fill_deviation_bps=average_deviation,
        worst_fill_deviation_bps=worst_deviation,
        protection_slo_breach_count=protection_slo_breach_count,
        current_unprotected_filled_intent_count=current_unprotected_filled_intent_count,
        reconciliation_mismatch_count=reconciliation_mismatch_count,
        rejected_order_count=rejected_order_count,
        duplicate_prevention_count=duplicate_prevention_count,
        acceptance_ready=False,
        blockers=tuple(blockers),
    )


__all__ = ["CanaryProofReport", "build_canary_proof_report"]
