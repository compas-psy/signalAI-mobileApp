"""Read-only forensic correlation for one immutable Lighter Canary snapshot.

The report is deliberately non-authorizing: it traces durable, non-secret
facts and fails closed as ``INCOMPLETE`` when a link is absent or mismatched.
It never performs provider I/O, reads secrets, changes execution state, or
creates promotion authority.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.canary_evidence import CanaryEvidenceReference
from ..models.canary_policy import CanaryPolicySnapshot, LighterCredentialGeneration
from ..models.execution import (
    ExecutionFill,
    ExecutionIntent,
    ExecutionModeActivationRequest,
    ExecutionModeEvent,
    ExecutionOrder,
    ExecutionProtection,
    ExecutionReconciliationEvent,
)
from .canary_activation import build_canary_mode_event_detail
from .canary_policy import CanaryPolicyError, verify_persisted_canary_snapshot
from .enums import ExecutionLifecycleMode

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_EVIDENCE_CATEGORIES = frozenset(
    {
        "strategy_performance",
        "shadow",
        "testnet",
        "protection_reconciliation",
        "kill_switch_drill",
        "security_scan",
        "operational_health",
    }
)


class CanaryCorrelationError(ValueError):
    """The requested immutable Canary snapshot cannot be correlated safely."""


@dataclass(frozen=True, slots=True)
class CanaryCorrelationReport:
    status: str
    snapshot_hash: str
    correlation_id: str
    source_sha: str
    engine_config_hash: str
    credential_generation_found: bool
    verified_evidence_ref_count: int
    activation_request_id: str | None
    mode_event_id: str | None
    execution_intent_count: int
    order_count: int
    fill_count: int
    active_protection_count: int
    reconciliation_event_count: int
    blockers: tuple[str, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "snapshot_hash": self.snapshot_hash,
            "correlation_id": self.correlation_id,
            "source_sha": self.source_sha,
            "engine_config_hash": self.engine_config_hash,
            "credential_generation_found": self.credential_generation_found,
            "verified_evidence_ref_count": self.verified_evidence_ref_count,
            "activation_request_id": self.activation_request_id,
            "mode_event_id": self.mode_event_id,
            "execution_intent_count": self.execution_intent_count,
            "order_count": self.order_count,
            "fill_count": self.fill_count,
            "active_protection_count": self.active_protection_count,
            "reconciliation_event_count": self.reconciliation_event_count,
            "blockers": list(self.blockers),
        }


def _exact_snapshot(db: Session, snapshot_hash: str) -> CanaryPolicySnapshot:
    normalized = str(snapshot_hash).strip().lower()
    if _HEX64.fullmatch(normalized) is None:
        raise CanaryCorrelationError("snapshot_hash must be a SHA-256 hex digest")
    snapshot = db.execute(
        select(CanaryPolicySnapshot).where(
            CanaryPolicySnapshot.snapshot_hash == normalized
        )
    ).scalar_one_or_none()
    if snapshot is None:
        raise CanaryCorrelationError("Canary policy snapshot does not exist")
    try:
        verify_persisted_canary_snapshot(snapshot)
    except CanaryPolicyError as exc:
        raise CanaryCorrelationError("Canary policy snapshot integrity failed") from exc
    return snapshot


def _credential_generation_bound(db: Session, snapshot: CanaryPolicySnapshot) -> bool:
    generation = db.execute(
        select(LighterCredentialGeneration).where(
            LighterCredentialGeneration.generation_id
            == snapshot.credential_generation_id
        )
    ).scalar_one_or_none()
    return bool(
        generation is not None
        and generation.action in {"CREATED", "ROTATED"}
        and generation.account_index == snapshot.account_index
        and generation.api_key_index == snapshot.api_key_index
    )


def _verified_evidence_ref_count(
    db: Session,
    snapshot: CanaryPolicySnapshot,
) -> tuple[int, bool]:
    payload = snapshot.payload_json
    if not isinstance(payload, Mapping):
        return 0, False
    refs = payload.get("evidence_refs")
    if not isinstance(refs, Mapping):
        return 0, False
    normalized_refs = {str(key): str(value) for key, value in refs.items()}
    if set(normalized_refs) != _REQUIRED_EVIDENCE_CATEGORIES:
        return 0, False

    verified = 0
    for category in sorted(_REQUIRED_EVIDENCE_CATEGORIES):
        ref = normalized_refs[category]
        row = db.execute(
            select(CanaryEvidenceReference).where(
                CanaryEvidenceReference.evidence_ref == ref
            )
        ).scalar_one_or_none()
        if row is None:
            continue
        if (
            row.category == category
            and row.verdict == "VERIFIED"
            and row.source_sha == snapshot.source_sha
            and row.engine_config_hash == snapshot.engine_config_hash
            and row.strategy_family == snapshot.strategy_family
            and row.strategy_version == snapshot.strategy_version
            and row.venue == "LIGHTER"
        ):
            verified += 1
    return verified, verified == len(_REQUIRED_EVIDENCE_CATEGORIES)


def _expected_owner_scope(
    snapshot: CanaryPolicySnapshot,
) -> tuple[str, Decimal, dict[str, object]] | None:
    payload = snapshot.payload_json
    if not isinstance(payload, Mapping) or payload.get("capital_currency") != "RUB":
        return None
    hard_caps = payload.get("hard_caps")
    if not isinstance(hard_caps, Mapping):
        return None
    try:
        capital = Decimal(str(payload.get("capital_amount")))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not capital.is_finite() or capital <= 0:
        return None
    return str(snapshot.account_index), capital, dict(hard_caps)


def _bound_activation_request(
    db: Session,
    snapshot: CanaryPolicySnapshot,
) -> ExecutionModeActivationRequest | None:
    request = db.execute(
        select(ExecutionModeActivationRequest).where(
            ExecutionModeActivationRequest.preview_hash == snapshot.snapshot_hash
        )
    ).scalar_one_or_none()
    expected_scope = _expected_owner_scope(snapshot)
    if request is None or expected_scope is None:
        return None
    expected_account, expected_capital_rub, expected_hard_caps = expected_scope
    blockers = request.blockers_json if isinstance(request.blockers_json, list) else None
    if not (
        request.from_mode is ExecutionLifecycleMode.SANDBOX
        and request.target_mode is ExecutionLifecycleMode.CANARY
        and request.outcome_mode is ExecutionLifecycleMode.CANARY
        and request.venue == "LIGHTER"
        and request.status == "APPLIED"
        and request.config_hash == snapshot.engine_config_hash
        and request.owner_confirmed_at is not None
        and request.account == expected_account
        and request.capital_rub == expected_capital_rub
        and request.hard_caps_json == expected_hard_caps
        and blockers == []
    ):
        return None
    return request


def _bound_mode_event(
    db: Session,
    *,
    snapshot: CanaryPolicySnapshot,
    activation: ExecutionModeActivationRequest | None,
) -> tuple[ExecutionModeEvent | None, bool]:
    """Return exact event plus whether a legacy/partial owner-scope event existed."""

    if activation is None or activation.owner_confirmed_at is None:
        return None, False
    expected_detail = build_canary_mode_event_detail(snapshot)
    partial_binding_seen = False
    events = db.execute(
        select(ExecutionModeEvent)
        .where(
            ExecutionModeEvent.from_mode == ExecutionLifecycleMode.SANDBOX,
            ExecutionModeEvent.to_mode == ExecutionLifecycleMode.CANARY,
            ExecutionModeEvent.occurred_at >= activation.owner_confirmed_at,
        )
        .order_by(ExecutionModeEvent.occurred_at, ExecutionModeEvent.id)
    ).scalars()
    for event in events:
        detail = event.detail_json if isinstance(event.detail_json, Mapping) else {}
        base_bound = (
            detail.get("canary_policy_snapshot_hash") == snapshot.snapshot_hash
            and detail.get("correlation_id") == snapshot.correlation_id
            and detail.get("source_sha") == snapshot.source_sha
            and detail.get("engine_config_hash") == snapshot.engine_config_hash
        )
        if not base_bound:
            continue
        partial_binding_seen = True
        if all(detail.get(key) == value for key, value in expected_detail.items()):
            return event, False
    return None, partial_binding_seen


def _canary_window_end(db: Session, *, event: ExecutionModeEvent):
    return db.execute(
        select(ExecutionModeEvent.occurred_at)
        .where(
            ExecutionModeEvent.from_mode == ExecutionLifecycleMode.CANARY,
            ExecutionModeEvent.to_mode != ExecutionLifecycleMode.CANARY,
            ExecutionModeEvent.occurred_at > event.occurred_at,
        )
        .order_by(ExecutionModeEvent.occurred_at, ExecutionModeEvent.id)
        .limit(1)
    ).scalar_one_or_none()


def _scoped_intents(
    db: Session,
    *,
    snapshot: CanaryPolicySnapshot,
    event: ExecutionModeEvent | None,
) -> tuple[ExecutionIntent, ...]:
    if event is None:
        return ()
    payload = snapshot.payload_json
    allowlist = payload.get("instrument_allowlist") if isinstance(payload, Mapping) else None
    if not isinstance(allowlist, list) or not allowlist:
        return ()
    instruments = tuple(str(value) for value in allowlist if str(value).strip())
    if not instruments:
        return ()

    statement = select(ExecutionIntent).where(
        ExecutionIntent.execution_mode_snapshot == ExecutionLifecycleMode.CANARY,
        ExecutionIntent.venue == "LIGHTER",
        ExecutionIntent.account == str(snapshot.account_index),
        ExecutionIntent.strategy_version == snapshot.strategy_version,
        ExecutionIntent.instrument_id.in_(instruments),
        ExecutionIntent.created_at >= event.occurred_at,
    )
    window_end = _canary_window_end(db, event=event)
    if window_end is not None:
        statement = statement.where(ExecutionIntent.created_at < window_end)
    return tuple(
        db.execute(statement.order_by(ExecutionIntent.created_at, ExecutionIntent.id)).scalars()
    )


def _execution_counts(
    db: Session,
    intents: tuple[ExecutionIntent, ...],
) -> tuple[int, int, int, int]:
    if not intents:
        return 0, 0, 0, 0
    ids = tuple(intent.id for intent in intents)
    order_count = len(
        tuple(
            db.execute(
                select(ExecutionOrder).where(ExecutionOrder.intent_id.in_(ids))
            ).scalars()
        )
    )
    fill_count = len(
        tuple(
            db.execute(
                select(ExecutionFill).where(ExecutionFill.intent_id.in_(ids))
            ).scalars()
        )
    )
    protections = tuple(
        db.execute(
            select(ExecutionProtection).where(
                ExecutionProtection.intent_id.in_(ids),
                ExecutionProtection.status == "ACTIVE",
                ExecutionProtection.armed_at.is_not(None),
            )
        ).scalars()
    )
    active_protection_count = sum(
        1 for row in protections if str(row.provider_order_id or "").strip()
    )
    reconciliation_event_count = len(
        tuple(
            db.execute(
                select(ExecutionReconciliationEvent).where(
                    ExecutionReconciliationEvent.intent_id.in_(ids)
                )
            ).scalars()
        )
    )
    return order_count, fill_count, active_protection_count, reconciliation_event_count


def build_canary_correlation_report(
    db: Session,
    *,
    snapshot_hash: str,
) -> CanaryCorrelationReport:
    snapshot = _exact_snapshot(db, snapshot_hash)
    blockers: list[str] = []

    credential_generation_found = _credential_generation_bound(db, snapshot)
    if not credential_generation_found:
        blockers.append("CREDENTIAL_GENERATION_BINDING_MISSING")

    verified_evidence_ref_count, evidence_complete = _verified_evidence_ref_count(
        db, snapshot
    )
    if not evidence_complete:
        blockers.append("CANARY_EVIDENCE_BINDING_INCOMPLETE")

    activation = _bound_activation_request(db, snapshot)
    if activation is None:
        blockers.append("ACTIVATION_REQUEST_BINDING_MISSING")

    mode_event, owner_scope_incomplete = _bound_mode_event(
        db, snapshot=snapshot, activation=activation
    )
    if mode_event is None:
        blockers.append(
            "CANARY_MODE_EVENT_OWNER_SCOPE_INCOMPLETE"
            if owner_scope_incomplete
            else "CANARY_MODE_EVENT_BINDING_MISSING"
        )

    intents = _scoped_intents(db, snapshot=snapshot, event=mode_event)
    if not intents:
        blockers.append("CANARY_EXECUTION_EVIDENCE_MISSING")
    order_count, fill_count, active_protection_count, reconciliation_event_count = (
        _execution_counts(db, intents)
    )
    if intents:
        if order_count == 0:
            blockers.append("CANARY_ORDER_EVIDENCE_MISSING")
        if fill_count == 0:
            blockers.append("CANARY_FILL_EVIDENCE_MISSING")
        if active_protection_count == 0:
            blockers.append("CANARY_ACTIVE_PROTECTION_EVIDENCE_MISSING")
        if reconciliation_event_count == 0:
            blockers.append("CANARY_RECONCILIATION_EVIDENCE_MISSING")

    return CanaryCorrelationReport(
        status="COMPLETE" if not blockers else "INCOMPLETE",
        snapshot_hash=snapshot.snapshot_hash,
        correlation_id=snapshot.correlation_id,
        source_sha=snapshot.source_sha,
        engine_config_hash=snapshot.engine_config_hash,
        credential_generation_found=credential_generation_found,
        verified_evidence_ref_count=verified_evidence_ref_count,
        activation_request_id=str(activation.id) if activation is not None else None,
        mode_event_id=str(mode_event.id) if mode_event is not None else None,
        execution_intent_count=len(intents),
        order_count=order_count,
        fill_count=fill_count,
        active_protection_count=active_protection_count,
        reconciliation_event_count=reconciliation_event_count,
        blockers=tuple(blockers),
    )


__all__ = [
    "CanaryCorrelationError",
    "CanaryCorrelationReport",
    "build_canary_correlation_report",
]
