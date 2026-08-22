from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import telegram_notifications as telegram
from app.execution.enums import ExecutionKillSwitchLevel
from app.models import AuditEvent, NotificationOutbox
from app.notification_outbox import list_after
from app.ops.backpressure import build_backpressure_plan
from app.ops.ollama_shed import OllamaShedResult, OllamaShedStatus
from app.ops.pressure import PressureAssessment, PressureState
from app.ops.remediation import record_resource_remediation
from app.ops.retention import RetentionResult, RetentionStatus


NOW = datetime(2026, 8, 18, 18, 0, tzinfo=UTC)


def _assessment(
    state: PressureState,
    *reasons: str,
) -> PressureAssessment:
    return PressureAssessment(
        state=state,
        score=5 if state not in {PressureState.NORMAL, PressureState.RECOVERING} else 0,
        reasons=tuple(reasons),
        active_dimensions=2 if reasons else 0,
    )


def _ollama(status: OllamaShedStatus) -> OllamaShedResult:
    return OllamaShedResult(
        status=status,
        attempted=status in {OllamaShedStatus.UNLOADED, OllamaShedStatus.FAILED},
        model="qwen3.5:4b" if status is not OllamaShedStatus.NOT_REQUIRED else None,
        detail=status.value,
    )


def _retention(
    status: RetentionStatus,
    *,
    deleted_files: int = 0,
    deleted_bytes: int = 0,
) -> RetentionResult:
    return RetentionResult(
        status=status,
        candidate_files=deleted_files,
        candidate_bytes=deleted_bytes,
        deleted_files=deleted_files,
        deleted_bytes=deleted_bytes,
    )


def test_pressure_remediation_is_append_only_audited_and_queued_for_owner(session: Session):
    assessment = _assessment(
        PressureState.PRESSURE,
        "memory_headroom_pressure",
        "resource_trend_worsening",
    )
    plan = build_backpressure_plan(state=PressureState.PRESSURE)

    result = record_resource_remediation(
        session,
        assessment=assessment,
        plan=plan,
        ollama=_ollama(OllamaShedStatus.UNLOADED),
        retention=_retention(RetentionStatus.NOT_REQUIRED),
        now=NOW,
    )
    session.flush()

    assert result.recorded is True
    assert result.audit_id is not None
    assert result.notification_id is not None

    audit = session.get(AuditEvent, result.audit_id)
    assert audit is not None
    assert audit.actor == "resource-autopilot"
    assert audit.action == "RESOURCE_REMEDIATION"
    assert audit.subject == "resource-capacity"
    assert audit.after_json["pressure_state"] == "PRESSURE"
    assert audit.after_json["effective_state"] == "PRESSURE"
    assert audit.after_json["new_entries"] == "ALLOW"
    assert audit.after_json["ollama"]["status"] == "UNLOADED"
    assert audit.after_json["retention"]["status"] == "NOT_REQUIRED"
    assert audit.after_json["fingerprint"] == result.fingerprint

    event = session.get(NotificationOutbox, result.notification_id)
    assert event is not None
    assert event.kind == "RESOURCE"
    assert event.dedup_key == f"resource-remediation:{audit.id}"
    assert "PRESSURE" in event.title
    assert "Ollama: UNLOADED" in event.body


def test_identical_latest_resource_state_is_deduplicated_across_calls(session: Session):
    assessment = _assessment(PressureState.PRESSURE, "disk_headroom_pressure")
    plan = build_backpressure_plan(state=PressureState.PRESSURE)
    kwargs = dict(
        assessment=assessment,
        plan=plan,
        ollama=_ollama(OllamaShedStatus.UNLOADED),
        retention=_retention(
            RetentionStatus.CLEANED,
            deleted_files=2,
            deleted_bytes=1024,
        ),
        now=NOW,
    )

    first = record_resource_remediation(session, **kwargs)
    session.flush()
    second = record_resource_remediation(session, **kwargs)
    session.flush()

    assert first.recorded is True
    assert second.recorded is False
    assert second.audit_id == first.audit_id
    assert second.notification_id is None
    assert len(
        list(
            session.execute(
                select(AuditEvent).where(AuditEvent.action == "RESOURCE_REMEDIATION")
            ).scalars()
        )
    ) == 1
    assert len(
        list(
            session.execute(
                select(NotificationOutbox).where(NotificationOutbox.kind == "RESOURCE")
            ).scalars()
        )
    ) == 1


def test_initial_normal_state_is_silent_but_recovery_after_pressure_is_recorded(session: Session):
    normal = _assessment(PressureState.NORMAL)
    normal_plan = build_backpressure_plan(state=PressureState.NORMAL)

    initial = record_resource_remediation(
        session,
        assessment=normal,
        plan=normal_plan,
        ollama=_ollama(OllamaShedStatus.NOT_REQUIRED),
        retention=_retention(RetentionStatus.NOT_REQUIRED),
        now=NOW,
    )
    assert initial.recorded is False

    pressured = record_resource_remediation(
        session,
        assessment=_assessment(PressureState.PRESSURE, "memory_headroom_pressure"),
        plan=build_backpressure_plan(state=PressureState.PRESSURE),
        ollama=_ollama(OllamaShedStatus.UNLOADED),
        retention=_retention(RetentionStatus.NOT_REQUIRED),
        now=NOW,
    )
    session.flush()
    assert pressured.recorded is True

    recovered = record_resource_remediation(
        session,
        assessment=normal,
        plan=normal_plan,
        ollama=_ollama(OllamaShedStatus.NOT_REQUIRED),
        retention=_retention(RetentionStatus.NOT_REQUIRED),
        now=NOW,
    )
    session.flush()

    assert recovered.recorded is True
    audit = session.get(AuditEvent, recovered.audit_id)
    assert audit is not None
    assert audit.after_json["pressure_state"] == "NORMAL"
    event = session.get(NotificationOutbox, recovered.notification_id)
    assert event is not None
    assert "восстанов" in event.title.lower()


def test_enabled_retention_job_audits_initial_not_required_skip(session: Session):
    result = record_resource_remediation(
        session,
        assessment=_assessment(PressureState.NORMAL),
        plan=build_backpressure_plan(state=PressureState.NORMAL),
        ollama=_ollama(OllamaShedStatus.NOT_REQUIRED),
        retention=_retention(RetentionStatus.NOT_REQUIRED),
        now=NOW,
        force_audit=True,
    )

    assert result.recorded is True
    assert session.get(AuditEvent, result.audit_id) is not None


def test_forced_retention_attempts_are_not_fingerprint_deduplicated(session: Session):
    assessment = _assessment(PressureState.PRESSURE, "disk_headroom_pressure")
    plan = build_backpressure_plan(state=PressureState.PRESSURE)
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()

    first = record_resource_remediation(
        session,
        assessment=assessment,
        plan=plan,
        ollama=_ollama(OllamaShedStatus.UNLOADED),
        retention=_retention(RetentionStatus.CLEANED, deleted_files=1, deleted_bytes=128),
        now=NOW,
        force_audit=True,
        retention_attempt_id=first_id,
    )
    second = record_resource_remediation(
        session,
        assessment=assessment,
        plan=plan,
        ollama=_ollama(OllamaShedStatus.UNLOADED),
        retention=_retention(RetentionStatus.CLEANED, deleted_files=1, deleted_bytes=128),
        now=NOW,
        force_audit=True,
        retention_attempt_id=second_id,
    )
    session.flush()

    assert first.recorded is True
    assert second.recorded is True
    audits = list(
        session.execute(
            select(AuditEvent)
            .where(AuditEvent.action == "RESOURCE_REMEDIATION")
            .order_by(AuditEvent.id)
        ).scalars()
    )
    assert len(audits) == 2
    assert {audit.after_json["retention_attempt_id"] for audit in audits} == {
        str(first_id),
        str(second_id),
    }


def test_entry_halt_is_applied_without_changing_legacy_execution_mode(session: Session):
    from app.models import RiskState

    # A migrated database does not seed the singleton RiskState row. Create an
    # explicit baseline so this test proves CRITICAL resource pressure changes
    # only the approved entry-safety state and does not mutate legacy mode.
    session.add(RiskState(id=1))
    session.flush()
    before = session.get(RiskState, 1)
    assert before is not None
    before_mode = str(before.execution_mode)

    result = record_resource_remediation(
        session,
        assessment=_assessment(PressureState.CRITICAL, "oom_kill_detected"),
        plan=build_backpressure_plan(state=PressureState.CRITICAL),
        ollama=_ollama(OllamaShedStatus.UNLOADED),
        retention=_retention(RetentionStatus.NOT_REQUIRED),
        now=NOW,
    )
    session.flush()

    audit = session.get(AuditEvent, result.audit_id)
    assert audit is not None
    assert audit.after_json["new_entries"] == "HALT_NEW_ENTRIES"
    after = session.get(RiskState, 1)
    assert after is not None
    assert str(after.execution_mode) == before_mode
    assert after.kill_switch is True
    assert after.kill_switch_level == ExecutionKillSwitchLevel.HALT_NEW_ENTRIES
    event = session.get(NotificationOutbox, result.notification_id)
    assert event is not None
    assert "применено автоматически" in event.body
    assert "только рекомендация" not in event.body


def test_resource_outbox_event_is_delivered_to_telegram_as_text(
    session: Session,
    monkeypatch,
):
    monkeypatch.setenv("TELEGRAM_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHATID", "123")
    telegram.status(session)

    result = record_resource_remediation(
        session,
        assessment=_assessment(PressureState.PRESSURE, "disk_headroom_pressure"),
        plan=build_backpressure_plan(state=PressureState.PRESSURE),
        ollama=_ollama(OllamaShedStatus.UNLOADED),
        retention=_retention(
            RetentionStatus.CLEANED,
            deleted_files=3,
            deleted_bytes=4096,
        ),
        now=NOW,
    )
    session.flush()
    assert result.notification_id is not None

    sent: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        telegram,
        "_telegram_request",
        lambda method, fields, **_: sent.append((method, fields)) or {"ok": True},
    )

    assert telegram.deliver(session) == 1
    assert telegram.deliver(session) == 0
    assert sent[0][0] == "sendMessage"
    assert "PRESSURE" in sent[0][1]["text"]
    assert "Retention: CLEANED" in sent[0][1]["text"]


def test_resource_alert_remains_visible_in_durable_outbox_even_without_telegram_secrets(
    session: Session,
    monkeypatch,
):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHATID", raising=False)

    result = record_resource_remediation(
        session,
        assessment=_assessment(PressureState.PRESSURE, "memory_headroom_pressure"),
        plan=build_backpressure_plan(state=PressureState.PRESSURE),
        ollama=_ollama(OllamaShedStatus.FAILED),
        retention=_retention(RetentionStatus.NOT_REQUIRED),
        now=NOW,
    )
    session.flush()

    assert telegram.deliver(session) == 0
    events = list_after(session, 0)
    resource = next(event for event in events if event.id == result.notification_id)
    assert resource.kind == "RESOURCE"
    assert "FAILED" in resource.body
