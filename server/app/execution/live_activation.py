"""Replay-safe two-step owner activation for LIVE execution (SAI-032 / B6.3).

The public flow never accepts promotion proof flags from the mobile client.
Preview stores exactly the venue/account/capital/hard caps the owner was shown;
confirmation re-reads current server context and gates before any mode change.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Callable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..config import get_config
from ..models.execution import ExecutionModeActivationRequest
from ..models.risk import AuditEvent
from .enums import ExecutionLifecycleMode
from .mode import (
    ExecutionModeChangeRejected,
    ModeChangeAuthorization,
    change_execution_mode,
    get_execution_mode,
)
from .promotion_guard import (
    PromotionDecision,
    PromotionEvidence,
    current_server_promotion_evidence,
    evaluate_promotion,
)
from .promotion_evidence import PromotionEvidenceScope, record_promotion_evidence_decision


class LiveActivationRejected(ValueError):
    """The activation request is malformed, stale, replayed or unsafe."""


@dataclass(frozen=True)
class LiveActivationContext:
    venue: str
    account: str
    capital_rub: Decimal
    hard_caps: dict[str, str]
    config_hash: str
    paper_only: bool


@dataclass(frozen=True)
class LiveActivationPreview:
    preview_hash: str
    from_mode: ExecutionLifecycleMode
    target_mode: ExecutionLifecycleMode
    venue: str
    account: str
    capital_rub: Decimal
    hard_caps: dict[str, str]
    config_hash: str
    allowed: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class LiveActivationResult:
    preview_hash: str
    idempotency_key: str
    status: str
    mode: ExecutionLifecycleMode
    blockers: tuple[str, ...]


ContextProvider = Callable[
    [Session, ExecutionLifecycleMode, ExecutionLifecycleMode],
    LiveActivationContext,
]
EvidenceProvider = Callable[
    [Session, ExecutionLifecycleMode, ExecutionLifecycleMode],
    PromotionEvidence,
]


def _default_context(
    db: Session,
    current: ExecutionLifecycleMode,
    target: ExecutionLifecycleMode,
) -> LiveActivationContext:
    del db, current, target
    cfg = get_config()
    venue = str(cfg.get("execution.live_venue", "NOT_CONFIGURED")).strip()
    account = str(cfg.get("execution.live_account", "NOT_CONFIGURED")).strip()
    if not venue:
        venue = "NOT_CONFIGURED"
    if not account:
        account = "NOT_CONFIGURED"
    return LiveActivationContext(
        venue=venue,
        account=account,
        capital_rub=cfg.decimal("risk.equity_rub"),
        hard_caps={
            "max_risk_per_trade": str(cfg.decimal("risk.max_risk_per_trade")),
            "max_total_open_risk": str(cfg.decimal("risk.max_total_open_risk")),
            "max_cluster_risk": str(cfg.decimal("risk.max_cluster_risk")),
            "daily_loss_limit": str(cfg.decimal("risk.daily_loss_limit")),
            "weekly_loss_limit": str(cfg.decimal("risk.weekly_loss_limit")),
            "monthly_loss_limit": str(cfg.decimal("risk.monthly_loss_limit")),
            "max_leverage": str(cfg.decimal("risk.max_crypto_leverage")),
            "min_liquidation_distance_ratio": str(
                cfg.decimal("risk.min_liquidation_distance_ratio")
            ),
        },
        config_hash=cfg.config_hash,
        paper_only=bool(cfg.get("risk.paper_only")),
    )


def _default_evidence(
    db: Session,
    current: ExecutionLifecycleMode,
    target: ExecutionLifecycleMode,
) -> PromotionEvidence:
    return current_server_promotion_evidence(
        db,
        current=current,
        target=target,
    )


def _activation_evidence_scope(context: LiveActivationContext) -> PromotionEvidenceScope:
    """Server-derived correlation scope; default evidence still blocks LIVE."""

    return PromotionEvidenceScope(
        strategy_family="EXECUTION_LIFECYCLE",
        strategy_version="activation_v1",
        venue=context.venue,
        source_hash=hashlib.sha256(b"execution-live-activation/v1").hexdigest(),
        config_hash=context.config_hash,
        policy_hash=hashlib.sha256(b"ADR-0001").hexdigest(),
    )


def _activation_blockers(context: LiveActivationContext) -> tuple[str, ...]:
    blockers: list[str] = []
    if context.paper_only:
        blockers.append("risk.paper_only=true")
    if context.venue == "NOT_CONFIGURED" or context.account == "NOT_CONFIGURED":
        blockers.append("execution venue/account not configured")
    return tuple(blockers)


def _canonical_preview_payload(
    *,
    current: ExecutionLifecycleMode,
    context: LiveActivationContext,
    blockers: tuple[str, ...],
    nonce: str,
) -> str:
    payload = {
        "from_mode": current.value,
        "target_mode": ExecutionLifecycleMode.LIVE.value,
        "venue": context.venue,
        "account": context.account,
        "capital_rub": str(context.capital_rub),
        "hard_caps": context.hard_caps,
        "config_hash": context.config_hash,
        "blockers": list(blockers),
        "nonce": nonce,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _row_preview(row: ExecutionModeActivationRequest) -> LiveActivationPreview:
    return LiveActivationPreview(
        preview_hash=row.preview_hash,
        from_mode=ExecutionLifecycleMode(row.from_mode),
        target_mode=ExecutionLifecycleMode(row.target_mode),
        venue=row.venue,
        account=row.account,
        capital_rub=row.capital_rub,
        hard_caps=dict(row.hard_caps_json),
        config_hash=row.config_hash,
        allowed=not bool(row.blockers_json),
        blockers=tuple(str(item) for item in row.blockers_json),
    )


def _row_result(row: ExecutionModeActivationRequest) -> LiveActivationResult:
    if not row.idempotency_key:
        raise LiveActivationRejected("activation confirmation is not complete")
    outcome = row.outcome_mode or row.from_mode
    return LiveActivationResult(
        preview_hash=row.preview_hash,
        idempotency_key=row.idempotency_key,
        status=row.status,
        mode=ExecutionLifecycleMode(outcome),
        blockers=tuple(str(item) for item in row.blockers_json),
    )


def create_live_activation_preview(
    db: Session,
    *,
    context_provider: ContextProvider = _default_context,
    evidence_provider: EvidenceProvider = _default_evidence,
) -> LiveActivationPreview:
    """Persist the exact owner-visible confirmation context for CANARY→LIVE."""

    current = get_execution_mode(db).mode
    target = ExecutionLifecycleMode.LIVE
    context = context_provider(db, current, target)
    scope = _activation_evidence_scope(context)
    evidence = (
        current_server_promotion_evidence(db, current=current, target=target, scope=scope)
        if evidence_provider is _default_evidence
        else evidence_provider(db, current, target)
    )
    decision = evaluate_promotion(
        current=current,
        target=target,
        evidence=evidence,
    )
    blockers = tuple(decision.blockers) + _activation_blockers(context)
    nonce = secrets.token_hex(32)
    canonical = _canonical_preview_payload(
        current=current,
        context=context,
        blockers=blockers,
        nonce=nonce,
    )
    preview_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    row = ExecutionModeActivationRequest(
        preview_hash=preview_hash,
        from_mode=current,
        target_mode=target,
        venue=context.venue,
        account=context.account,
        capital_rub=context.capital_rub,
        hard_caps_json=dict(context.hard_caps),
        blockers_json=list(blockers),
        config_hash=context.config_hash,
        status="PREVIEWED",
    )
    db.add(row)
    db.flush()
    return _row_preview(row)


def _idempotency_lock_key(idempotency_key: str) -> int:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _lock_idempotency_key(db: Session, idempotency_key: str) -> None:
    # Transaction-scoped advisory lock makes two different previews racing with
    # the same replay key deterministic before the unique constraint fires.
    db.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _idempotency_lock_key(idempotency_key)},
    ).scalar_one()


def _displayed_context_changed(
    row: ExecutionModeActivationRequest,
    current: LiveActivationContext,
) -> bool:
    return (
        row.venue != current.venue
        or row.account != current.account
        or row.capital_rub != current.capital_rub
        or dict(row.hard_caps_json) != current.hard_caps
    )


def _audit_confirmation(
    db: Session,
    *,
    row: ExecutionModeActivationRequest,
    promotion_evidence_correlation_id: str,
    evidence_snapshot_ids: tuple[str, ...],
) -> None:
    db.add(
        AuditEvent(
            actor="owner",
            action="execution_live_activation_confirm",
            subject=row.preview_hash,
            detail=row.status,
            before_json={
                "from_mode": ExecutionLifecycleMode(row.from_mode).value,
                "target_mode": ExecutionLifecycleMode(row.target_mode).value,
            },
            after_json={
                "status": row.status,
                "outcome_mode": (
                    ExecutionLifecycleMode(row.outcome_mode).value
                    if row.outcome_mode is not None
                    else ExecutionLifecycleMode(row.from_mode).value
                ),
                "blockers": list(row.blockers_json),
                "promotion_evidence_correlation_id": promotion_evidence_correlation_id,
                "promotion_evidence_snapshot_ids": list(evidence_snapshot_ids),
            },
        )
    )


def confirm_live_activation(
    db: Session,
    *,
    preview_hash: str,
    idempotency_key: str,
    owner_confirmed: bool,
    context_provider: ContextProvider = _default_context,
    evidence_provider: EvidenceProvider = _default_evidence,
) -> LiveActivationResult:
    """Second confirmation with replay protection and authoritative recheck."""

    preview_hash = preview_hash.strip()
    idempotency_key = idempotency_key.strip()
    if not preview_hash:
        raise LiveActivationRejected("preview hash is required")
    if not idempotency_key:
        raise LiveActivationRejected("idempotency key is required")
    if not owner_confirmed:
        raise LiveActivationRejected("explicit owner confirmation is required")

    _lock_idempotency_key(db, idempotency_key)
    prior = db.execute(
        select(ExecutionModeActivationRequest).where(
            ExecutionModeActivationRequest.idempotency_key == idempotency_key
        )
    ).scalar_one_or_none()
    if prior is not None:
        if prior.preview_hash != preview_hash:
            raise LiveActivationRejected(
                "idempotency key was already used for a different activation preview"
            )
        return _row_result(prior)

    row = db.execute(
        select(ExecutionModeActivationRequest)
        .where(ExecutionModeActivationRequest.preview_hash == preview_hash)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise LiveActivationRejected("activation preview does not exist")
    if row.idempotency_key is not None:
        raise LiveActivationRejected(
            "activation preview was already confirmed with a different idempotency key"
        )

    current_mode = get_execution_mode(db).mode
    target = ExecutionLifecycleMode.LIVE
    context = context_provider(db, current_mode, target)
    scope = _activation_evidence_scope(context)
    blockers: list[str] = []
    if current_mode != ExecutionLifecycleMode(row.from_mode):
        blockers.append("activation preview is stale: mode changed")
    if context.config_hash != row.config_hash:
        blockers.append("activation preview is stale: config changed")
    if _displayed_context_changed(row, context):
        blockers.append("activation preview is stale: displayed context changed")

    if not blockers:
        base_evidence = (
            current_server_promotion_evidence(
                db, current=current_mode, target=target, scope=scope
            )
            if evidence_provider is _default_evidence
            else evidence_provider(db, current_mode, target)
        )
        confirmed_evidence = replace(base_evidence, owner_confirmed=True)
        decision = evaluate_promotion(
            current=current_mode,
            target=target,
            evidence=confirmed_evidence,
        )
        blockers.extend(decision.blockers)
        blockers.extend(_activation_blockers(context))
    else:
        decision = None

    decision_for_audit = PromotionDecision(
        current=current_mode,
        target=target,
        allowed=not bool(blockers),
        blockers=tuple(blockers),
        evidence_notes=(decision.evidence_notes if decision is not None else ()),
        authorization=(decision.authorization if decision is not None else None),
        evidence_snapshot_ids=(
            decision.evidence_snapshot_ids if decision is not None else ()
        ),
    )
    promotion_evidence_correlation_id = record_promotion_evidence_decision(
        db,
        decision=decision_for_audit,
        scope=scope,
        actor="live-activation",
    )

    row.idempotency_key = idempotency_key
    row.owner_confirmed_at = datetime.now(UTC)
    row.blockers_json = list(blockers)
    row.updated_at = datetime.now(UTC)

    if blockers:
        row.status = "BLOCKED"
        row.outcome_mode = current_mode
        _audit_confirmation(
            db,
            row=row,
            promotion_evidence_correlation_id=promotion_evidence_correlation_id,
            evidence_snapshot_ids=decision_for_audit.evidence_snapshot_ids,
        )
        db.flush()
        return _row_result(row)

    if decision is None or decision.authorization is None:
        raise LiveActivationRejected("promotion guard did not authorize LIVE activation")

    authorization = ModeChangeAuthorization(
        allowed=True,
        actor=decision.authorization.actor,
        reason=decision.authorization.reason,
        detail_json={
            **decision.authorization.detail_json,
            "activation_preview_hash": row.preview_hash,
            "activation_idempotency_key": idempotency_key,
            "activation_venue": row.venue,
            "activation_account": row.account,
            "promotion_evidence_correlation_id": promotion_evidence_correlation_id,
            "promotion_evidence_snapshot_ids": list(
                decision_for_audit.evidence_snapshot_ids
            ),
        },
    )
    try:
        snapshot = change_execution_mode(
            db,
            target=target,
            actor="owner",
            reason="two-step LIVE activation confirmed",
            authorization=authorization,
        )
    except ExecutionModeChangeRejected as exc:
        raise LiveActivationRejected(str(exc)) from exc

    row.status = "APPLIED"
    row.outcome_mode = snapshot.mode
    _audit_confirmation(
        db,
        row=row,
        promotion_evidence_correlation_id=promotion_evidence_correlation_id,
        evidence_snapshot_ids=decision_for_audit.evidence_snapshot_ids,
    )
    db.flush()
    return _row_result(row)


__all__ = [
    "LiveActivationContext",
    "LiveActivationPreview",
    "LiveActivationRejected",
    "LiveActivationResult",
    "confirm_live_activation",
    "create_live_activation_preview",
]
