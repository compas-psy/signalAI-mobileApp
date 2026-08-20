"""SAI-046 append-only forensic audit for manual-risk owner decisions.

The existing ``AuditEvent`` table is the canonical audit ledger. Successful
preview/apply outcomes are appended in the caller transaction so they are
atomic with the business result. Rejected apply outcomes use a separate short
transaction because the HTTP 409 intentionally rolls the request transaction
back; without that boundary a rejection audit would be illusory.

Raw signed preview tokens and raw idempotency keys are never persisted here.
Only SHA-256 digests are stored for correlation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Callable

from sqlalchemy.orm import Session, sessionmaker

from ..db import get_session_factory
from ..models.risk import AuditEvent
from .manual_preview import ManualRiskPreview


@dataclass(frozen=True, slots=True)
class ManualRiskAuditRecord:
    action: str
    subject: str
    outcome: str
    detail: str
    context: dict[str, object]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(Decimal(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _iso_z(value: datetime) -> str:
    instant = value
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _enum_text(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _margin_proof_hash(preview: ManualRiskPreview) -> str | None:
    try:
        payload = json.loads(preview.proof_payload_json)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("margin_proof_hash")
    return str(raw) if raw not in (None, "") else None


def preview_audit_record(preview: ManualRiskPreview) -> ManualRiskAuditRecord:
    """Build the exact server-owned owner-view audit record for one preview."""

    token_digest = sha256_text(preview.preview_hash) if preview.preview_hash else None
    context: dict[str, object] = {
        "risk_snapshot_id": str(preview.risk_snapshot_id),
        "preset_id": preview.preset_id,
        "execution_mode": _enum_text(preview.execution_mode),
        "execution_venue": preview.execution_venue,
        "execution_account": preview.execution_account,
        "auto_risk_pct": _decimal_text(preview.auto_risk_pct),
        "auto_risk_amount": _decimal_text(preview.auto_risk_amount),
        "requested_risk_pct": _decimal_text(preview.requested_risk_pct),
        "requested_risk_amount": _decimal_text(preview.requested_risk_amount),
        "effective_risk_pct": _decimal_text(preview.effective_risk_pct),
        "effective_risk_amount": _decimal_text(preview.effective_risk_amount),
        "hard_cap_risk_pct": _decimal_text(preview.hard_cap_risk_pct),
        "effective_quantity": _decimal_text(preview.quantity),
        "notional": _decimal_text(preview.notional),
        "resulting_leverage": _decimal_text(preview.resulting_leverage),
        "liquidation_distance_ratio": _decimal_text(
            preview.liquidation_distance_ratio
        ),
        "total_open_risk_after": _decimal_text(preview.total_open_risk_after),
        "cluster_risk_after": _decimal_text(preview.cluster_risk_after),
        "worst_case_stop_loss": _decimal_text(preview.worst_case_stop_loss),
        "binding_constraint": preview.binding_constraint,
        "warnings": list(preview.warnings),
        "blockers": list(preview.blockers),
        "issued_at": _iso_z(preview.issued_at),
        "expires_at": _iso_z(preview.expires_at),
        "preview_token_sha256": token_digest,
        "margin_proof_hash": _margin_proof_hash(preview),
    }
    return ManualRiskAuditRecord(
        action="manual_risk_previewed",
        subject=str(preview.idea_id),
        outcome="ALLOWED" if preview.allowed else "BLOCKED",
        detail="manual risk preview calculated from server-owned state",
        context=context,
    )


def apply_audit_record(
    *,
    idea_id: object,
    preset_id: str,
    current_mode: object,
    preview_token: str,
    idempotency_key: str,
    outcome: str,
    owner_reason: str,
    override: object | None = None,
    rejection_detail: str | None = None,
) -> ManualRiskAuditRecord:
    """Build one apply-attempt audit record without storing replay credentials."""

    context: dict[str, object] = {
        "preset_id": preset_id.strip().upper(),
        "execution_mode": _enum_text(current_mode),
        "preview_token_sha256": sha256_text(preview_token.strip()),
        "idempotency_key_sha256": sha256_text(idempotency_key.strip()),
        "owner_reason": owner_reason.strip(),
    }
    if override is not None:
        context.update(
            {
                "override_id": str(getattr(override, "id")),
                "risk_snapshot_id": str(getattr(override, "risk_snapshot_id")),
                "execution_venue": str(getattr(override, "venue")),
                "execution_account": str(getattr(override, "account")),
                "effective_risk_pct": _decimal_text(
                    Decimal(getattr(override, "effective_risk_pct"))
                ),
                "effective_quantity": _decimal_text(
                    Decimal(getattr(override, "effective_quantity"))
                ),
                "effective_leverage": _decimal_text(
                    Decimal(getattr(override, "effective_leverage"))
                    if getattr(override, "effective_leverage") is not None
                    else None
                ),
                "hard_cap_risk_pct": _decimal_text(
                    Decimal(getattr(override, "hard_cap_risk_pct"))
                ),
                "hard_cap_leverage": _decimal_text(
                    Decimal(getattr(override, "hard_cap_leverage"))
                    if getattr(override, "hard_cap_leverage") is not None
                    else None
                ),
            }
        )
    if rejection_detail:
        context["rejection_detail"] = rejection_detail

    return ManualRiskAuditRecord(
        action="manual_risk_apply_outcome",
        subject=str(idea_id),
        outcome=outcome,
        detail=f"manual risk apply {outcome.lower()}",
        context=context,
    )


def append_manual_risk_audit(
    db: Session,
    record: ManualRiskAuditRecord,
) -> AuditEvent:
    """Append one audit fact inside the caller transaction."""

    payload = dict(record.context)
    payload["outcome"] = record.outcome
    event = AuditEvent(
        occurred_at=datetime.now(UTC),
        actor="owner",
        action=record.action,
        subject=record.subject,
        detail=record.detail,
        before_json={},
        after_json=payload,
    )
    db.add(event)
    db.flush()
    return event


def persist_manual_risk_audit(
    record: ManualRiskAuditRecord,
    *,
    session_factory: Callable[[], Session] | sessionmaker[Session] | None = None,
) -> None:
    """Durably append an audit fact in an independent transaction.

    This boundary is intentionally narrow and used for rejected apply attempts.
    Failure to persist the forensic record is not swallowed: the request must
    fail closed rather than claim a decision was safely audited when it was not.
    """

    factory = session_factory or get_session_factory()
    db = factory()
    try:
        append_manual_risk_audit(db, record)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


__all__ = [
    "ManualRiskAuditRecord",
    "append_manual_risk_audit",
    "apply_audit_record",
    "persist_manual_risk_audit",
    "preview_audit_record",
    "sha256_text",
]
