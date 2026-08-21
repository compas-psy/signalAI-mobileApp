from __future__ import annotations

import importlib
from dataclasses import replace
from decimal import Decimal

import pytest

from app.execution.enums import ExecutionLifecycleMode
from app.execution.mode import ModeChangeAuthorization, change_execution_mode, get_execution_mode
from app.execution.promotion_guard import PromotionEvidence
from app.models import AuditEvent, ExecutionModeEvent, PromotionEvidenceDecision


def _activation():
    return importlib.import_module("app.execution.live_activation")


def _activation_model():
    models = importlib.import_module("app.models.execution")
    model = getattr(models, "ExecutionModeActivationRequest", None)
    assert model is not None, "SAI-032 activation persistence model is missing"
    return model


def _set_mode(session, target: ExecutionLifecycleMode) -> None:
    current = get_execution_mode(session).mode
    if current == target:
        return
    change_execution_mode(
        session,
        target=target,
        actor="test",
        reason="test setup",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test setup authorization",
            detail_json={"test_only": True},
        ),
    )
    session.flush()


def _ready_context(activation):
    return activation.LiveActivationContext(
        venue="LIGHTER",
        account="canary-main",
        capital_rub=Decimal("10000"),
        hard_caps={
            "max_risk_per_trade": "0.0025",
            "max_total_open_risk": "0.005",
            "daily_loss_limit": "0.005",
            "max_leverage": "2.0",
        },
        config_hash="a" * 64,
        paper_only=False,
    )


def _ready_evidence(*_args, **_kwargs):
    return PromotionEvidence(
        adr_gates_passed=True,
        performance_gates_passed=True,
        ops_gates_passed=True,
        notes=("test readiness provider",),
    )


def test_preview_is_durable_and_shows_exact_owner_confirmation_context(session):
    activation = _activation()
    model = _activation_model()
    _set_mode(session, ExecutionLifecycleMode.CANARY)

    preview = activation.create_live_activation_preview(session)
    session.flush()

    assert preview.from_mode == ExecutionLifecycleMode.CANARY
    assert preview.target_mode == ExecutionLifecycleMode.LIVE
    assert preview.venue == "NOT_CONFIGURED"
    assert preview.account == "NOT_CONFIGURED"
    assert preview.capital_rub == Decimal("300000")
    assert preview.hard_caps["max_risk_per_trade"] == "0.0075"
    assert preview.hard_caps["max_total_open_risk"] == "0.02"
    assert preview.hard_caps["daily_loss_limit"] == "0.015"
    assert preview.hard_caps["max_leverage"] == "3.0"
    assert len(preview.preview_hash) == 64
    assert preview.allowed is False
    assert "risk.paper_only=true" in preview.blockers
    assert "execution venue/account not configured" in preview.blockers

    row = session.query(model).filter_by(preview_hash=preview.preview_hash).one()
    assert row.from_mode == ExecutionLifecycleMode.CANARY
    assert row.target_mode == ExecutionLifecycleMode.LIVE
    assert row.status == "PREVIEWED"
    assert row.config_hash == preview.config_hash


def test_confirm_requires_explicit_second_owner_confirmation(session):
    activation = _activation()
    _set_mode(session, ExecutionLifecycleMode.CANARY)
    preview = activation.create_live_activation_preview(session)

    with pytest.raises(activation.LiveActivationRejected, match="owner confirmation"):
        activation.confirm_live_activation(
            session,
            preview_hash=preview.preview_hash,
            idempotency_key="confirm-no-owner",
            owner_confirmed=False,
        )

    row = session.query(_activation_model()).filter_by(
        preview_hash=preview.preview_hash
    ).one()
    assert row.idempotency_key is None
    assert row.status == "PREVIEWED"


def test_confirm_rechecks_server_gates_instead_of_trusting_preview(session):
    activation = _activation()
    _set_mode(session, ExecutionLifecycleMode.CANARY)
    context = _ready_context(activation)
    preview = activation.create_live_activation_preview(
        session,
        context_provider=lambda *_: context,
        evidence_provider=_ready_evidence,
    )
    assert preview.blockers == ("explicit owner confirmation missing",)

    def degraded_evidence(*_args, **_kwargs):
        return PromotionEvidence(
            adr_gates_passed=True,
            performance_gates_passed=True,
            ops_gates_passed=False,
            notes=("ops degraded after preview",),
        )

    result = activation.confirm_live_activation(
        session,
        preview_hash=preview.preview_hash,
        idempotency_key="recheck-blocked",
        owner_confirmed=True,
        context_provider=lambda *_: context,
        evidence_provider=degraded_evidence,
    )
    session.flush()

    assert result.status == "BLOCKED"
    assert result.mode == ExecutionLifecycleMode.CANARY
    assert result.blockers == ("ops gates not verified",)
    assert get_execution_mode(session).mode == ExecutionLifecycleMode.CANARY
    assert session.query(ExecutionModeEvent).filter_by(
        to_mode=ExecutionLifecycleMode.LIVE
    ).count() == 0
    audit = session.query(AuditEvent).filter_by(
        action="execution_live_activation_confirm"
    ).one()
    correlation_id = audit.after_json["promotion_evidence_correlation_id"]
    decision_row = session.query(PromotionEvidenceDecision).filter_by(
        correlation_id=correlation_id
    ).one()
    assert decision_row.allowed is False
    assert decision_row.blockers_json == ["ops gates not verified"]


def test_confirm_changes_mode_only_after_second_confirmation_and_gate_recheck(session):
    activation = _activation()
    _set_mode(session, ExecutionLifecycleMode.CANARY)
    context = _ready_context(activation)
    preview = activation.create_live_activation_preview(
        session,
        context_provider=lambda *_: context,
        evidence_provider=_ready_evidence,
    )

    result = activation.confirm_live_activation(
        session,
        preview_hash=preview.preview_hash,
        idempotency_key="apply-once",
        owner_confirmed=True,
        context_provider=lambda *_: context,
        evidence_provider=_ready_evidence,
    )
    session.flush()

    assert result.status == "APPLIED"
    assert result.mode == ExecutionLifecycleMode.LIVE
    assert result.blockers == ()
    assert get_execution_mode(session).mode == ExecutionLifecycleMode.LIVE
    event = session.query(ExecutionModeEvent).order_by(
        ExecutionModeEvent.occurred_at.desc()
    ).first()
    assert event is not None
    assert event.from_mode == ExecutionLifecycleMode.CANARY
    assert event.to_mode == ExecutionLifecycleMode.LIVE
    assert event.detail_json["activation_preview_hash"] == preview.preview_hash


def test_retry_after_lost_response_is_idempotent_and_does_not_write_second_event(session):
    activation = _activation()
    _set_mode(session, ExecutionLifecycleMode.CANARY)
    context = _ready_context(activation)
    preview = activation.create_live_activation_preview(
        session,
        context_provider=lambda *_: context,
        evidence_provider=_ready_evidence,
    )

    first = activation.confirm_live_activation(
        session,
        preview_hash=preview.preview_hash,
        idempotency_key="lost-response-key",
        owner_confirmed=True,
        context_provider=lambda *_: context,
        evidence_provider=_ready_evidence,
    )
    session.flush()
    live_events_before = session.query(ExecutionModeEvent).filter_by(
        to_mode=ExecutionLifecycleMode.LIVE
    ).count()
    audits_before = session.query(AuditEvent).filter_by(
        action="execution_live_activation_confirm"
    ).count()

    second = activation.confirm_live_activation(
        session,
        preview_hash=preview.preview_hash,
        idempotency_key="lost-response-key",
        owner_confirmed=True,
        context_provider=lambda *_: replace(context, paper_only=True),
        evidence_provider=lambda *_args, **_kwargs: PromotionEvidence(),
    )
    session.flush()

    assert second == first
    assert session.query(ExecutionModeEvent).filter_by(
        to_mode=ExecutionLifecycleMode.LIVE
    ).count() == live_events_before
    assert session.query(AuditEvent).filter_by(
        action="execution_live_activation_confirm"
    ).count() == audits_before


def test_same_idempotency_key_cannot_confirm_a_different_preview(session):
    activation = _activation()
    _set_mode(session, ExecutionLifecycleMode.CANARY)
    context = _ready_context(activation)
    first_preview = activation.create_live_activation_preview(
        session,
        context_provider=lambda *_: context,
        evidence_provider=_ready_evidence,
    )
    second_preview = activation.create_live_activation_preview(
        session,
        context_provider=lambda *_: context,
        evidence_provider=_ready_evidence,
    )
    activation.confirm_live_activation(
        session,
        preview_hash=first_preview.preview_hash,
        idempotency_key="shared-key",
        owner_confirmed=True,
        context_provider=lambda *_: context,
        evidence_provider=_ready_evidence,
    )

    with pytest.raises(activation.LiveActivationRejected, match="idempotency"):
        activation.confirm_live_activation(
            session,
            preview_hash=second_preview.preview_hash,
            idempotency_key="shared-key",
            owner_confirmed=True,
            context_provider=lambda *_: context,
            evidence_provider=_ready_evidence,
        )


def test_config_or_mode_change_makes_preview_stale_before_activation(session):
    activation = _activation()
    _set_mode(session, ExecutionLifecycleMode.CANARY)
    context = _ready_context(activation)
    preview = activation.create_live_activation_preview(
        session,
        context_provider=lambda *_: context,
        evidence_provider=_ready_evidence,
    )

    stale_context = replace(context, config_hash="b" * 64)
    result = activation.confirm_live_activation(
        session,
        preview_hash=preview.preview_hash,
        idempotency_key="stale-config",
        owner_confirmed=True,
        context_provider=lambda *_: stale_context,
        evidence_provider=_ready_evidence,
    )

    assert result.status == "BLOCKED"
    assert "activation preview is stale: config changed" in result.blockers
    assert get_execution_mode(session).mode == ExecutionLifecycleMode.CANARY


def test_generic_mode_change_endpoint_cannot_bypass_two_step_live_activation(session):
    api = importlib.import_module("app.api.v1.execution")
    _set_mode(session, ExecutionLifecycleMode.CANARY)

    with pytest.raises(Exception) as exc_info:
        api.change_execution_mode(
            request=api.ExecutionModeChangeRequest(
                target=ExecutionLifecycleMode.LIVE,
                reason="bypass attempt",
            ),
            db=session,
        )

    assert getattr(exc_info.value, "status_code", None) == 409
    assert "two-step" in str(getattr(exc_info.value, "detail", "")).lower()
    assert get_execution_mode(session).mode == ExecutionLifecycleMode.CANARY


def test_live_activation_api_requires_preview_hash_confirmation_and_idempotency_key(session):
    api = importlib.import_module("app.api.v1.execution")
    _set_mode(session, ExecutionLifecycleMode.CANARY)

    preview = api.preview_live_activation(db=session)
    assert preview.target_mode == ExecutionLifecycleMode.LIVE
    assert preview.preview_hash
    assert preview.venue == "NOT_CONFIGURED"
    assert preview.capital_rub == Decimal("300000")

    result = api.confirm_live_activation(
        request=api.LiveActivationConfirmRequest(
            preview_hash=preview.preview_hash,
            owner_confirmed=True,
        ),
        idempotency_key="api-confirm-1",
        db=session,
    )

    assert result.status == "BLOCKED"
    assert result.mode == ExecutionLifecycleMode.CANARY
    assert result.idempotency_key == "api-confirm-1"
