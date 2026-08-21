"""Resource remediation audit, owner alerting and approved CRITICAL halt.

Most resource-autopilot actions remain low-priority operational remediation.
SAI-033 makes one deliberate exception: when the existing backpressure policy
returns ``HALT_NEW_ENTRIES`` for actual CRITICAL pressure, that advisory is now
applied through the durable execution kill switch. Recovery never clears the
halt automatically; resuming entries remains a separate explicit safety action.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..execution.automatic_safety import automatic_halt_new_entries
from ..models import AuditEvent
from ..notification_outbox import emit
from .backpressure import BackpressurePlan, EntryDisposition
from .ollama_shed import OllamaShedResult
from .pressure import PressureAssessment, PressureState
from .retention import RetentionResult


_ACTION = "RESOURCE_REMEDIATION"
_ACTOR = "resource-autopilot"
_SUBJECT = "resource-capacity"


@dataclass(frozen=True, slots=True)
class ResourceRemediationRecord:
    recorded: bool
    fingerprint: str
    audit_id: uuid.UUID | None = None
    notification_id: int | None = None


def record_resource_remediation(
    session: Session,
    *,
    assessment: PressureAssessment,
    plan: BackpressurePlan,
    ollama: OllamaShedResult,
    retention: RetentionResult,
    now: datetime,
    force_audit: bool = False,
    retention_attempt_id: uuid.UUID | None = None,
) -> ResourceRemediationRecord:
    """Persist one meaningful resource state transition/remediation outcome.

    Consecutive identical fingerprints are deduplicated from the database, so
    the same state is not re-alerted after a process restart. Initial NORMAL is
    intentionally silent; NORMAL after any prior resource event is a recovery
    transition and is recorded.

    A CRITICAL plan's ``HALT_NEW_ENTRIES`` is applied before audit deduplication.
    That ordering is intentional: a repeated CRITICAL observation must still
    restore fail-closed entry state if some external owner action cleared it,
    while an already-active equal/stronger switch remains idempotent.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not isinstance(force_audit, bool):
        raise ValueError("force_audit must be bool")
    if retention_attempt_id is not None and not isinstance(retention_attempt_id, uuid.UUID):
        raise ValueError("retention_attempt_id must be UUID or None")

    if plan.new_entries is EntryDisposition.HALT_NEW_ENTRIES:
        automatic_halt_new_entries(
            session,
            reason=_automatic_halt_reason(assessment),
        )

    payload = _payload(
        assessment=assessment,
        plan=plan,
        ollama=ollama,
        retention=retention,
        retention_attempt_id=retention_attempt_id,
    )
    fingerprint = _fingerprint(payload)
    payload["fingerprint"] = fingerprint

    latest = _latest_resource_audit(session)
    if latest is None and assessment.state is PressureState.NORMAL and not force_audit:
        return ResourceRemediationRecord(recorded=False, fingerprint=fingerprint)

    if (
        not force_audit
        and latest is not None
        and latest.after_json.get("fingerprint") == fingerprint
    ):
        return ResourceRemediationRecord(
            recorded=False,
            fingerprint=fingerprint,
            audit_id=latest.id,
        )

    audit = AuditEvent(
        occurred_at=now,
        actor=_ACTOR,
        action=_ACTION,
        subject=_SUBJECT,
        detail=_detail(assessment, ollama, retention),
        before_json=dict(latest.after_json) if latest is not None else {},
        after_json=payload,
        trace_id=(
            f"retention-{retention_attempt_id}"
            if retention_attempt_id is not None
            else f"resource-{fingerprint[:16]}"
        ),
    )
    session.add(audit)
    session.flush()

    notification_id = emit(
        session,
        key=f"resource-remediation:{audit.id}",
        kind="RESOURCE",
        title=_title(assessment.state),
        body=_body(assessment, plan, ollama, retention),
    )
    return ResourceRemediationRecord(
        recorded=True,
        fingerprint=fingerprint,
        audit_id=audit.id,
        notification_id=notification_id,
    )


def _automatic_halt_reason(assessment: PressureAssessment) -> str:
    reasons = ", ".join(assessment.reasons) if assessment.reasons else "unspecified"
    return f"resource autopilot CRITICAL: {reasons}"


def _latest_resource_audit(session: Session) -> AuditEvent | None:
    return session.execute(
        select(AuditEvent)
        .where(
            AuditEvent.action == _ACTION,
            AuditEvent.subject == _SUBJECT,
        )
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _payload(
    *,
    assessment: PressureAssessment,
    plan: BackpressurePlan,
    ollama: OllamaShedResult,
    retention: RetentionResult,
    retention_attempt_id: uuid.UUID | None,
) -> dict:
    payload = {
        "pressure_state": assessment.state.value,
        "pressure_score": assessment.score,
        "pressure_reasons": list(assessment.reasons),
        "active_dimensions": assessment.active_dimensions,
        "observed_state": plan.observed_state.value,
        "effective_state": plan.effective_state.value,
        "new_entries": plan.new_entries.value,
        "workloads": {
            kind.value: disposition.value
            for kind, disposition in sorted(
                plan.workloads.items(), key=lambda item: item[0].value
            )
        },
        "plan_reasons": list(plan.reasons),
        "ollama": {
            "status": ollama.status.value,
            "attempted": ollama.attempted,
            "model": ollama.model,
            "detail": ollama.detail,
        },
        "retention": {
            "status": retention.status.value,
            "candidate_files": retention.candidate_files,
            "candidate_bytes": retention.candidate_bytes,
            "deleted_files": retention.deleted_files,
            "deleted_bytes": retention.deleted_bytes,
            "errors": list(retention.errors),
        },
    }
    if retention_attempt_id is not None:
        payload["retention_attempt_id"] = str(retention_attempt_id)
    return payload


def _fingerprint(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _detail(
    assessment: PressureAssessment,
    ollama: OllamaShedResult,
    retention: RetentionResult,
) -> str:
    return (
        f"{assessment.state.value}; "
        f"ollama={ollama.status.value}; retention={retention.status.value}"
    )


def _title(state: PressureState) -> str:
    if state is PressureState.NORMAL:
        return "SignalAI · ресурсы восстановлены"
    return f"SignalAI · ресурсы {state.value}"


def _body(
    assessment: PressureAssessment,
    plan: BackpressurePlan,
    ollama: OllamaShedResult,
    retention: RetentionResult,
) -> str:
    reasons = ", ".join(assessment.reasons) if assessment.reasons else "—"
    retention_detail = retention.status.value
    if retention.deleted_files or retention.deleted_bytes:
        retention_detail += (
            f" · {retention.deleted_files} файлов / {retention.deleted_bytes} байт"
        )
    entry_suffix = (
        " (применено автоматически)"
        if plan.new_entries is EntryDisposition.HALT_NEW_ENTRIES
        else ""
    )
    return "\n".join(
        [
            f"Состояние: {assessment.state.value}",
            f"Причины: {reasons}",
            f"Ollama: {ollama.status.value}",
            f"Retention: {retention_detail}",
            f"Новые входы: {plan.new_entries.value}{entry_suffix}",
        ]
    )


__all__ = ["ResourceRemediationRecord", "record_resource_remediation"]
