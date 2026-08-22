from __future__ import annotations

import importlib

import pytest

from app.execution.enums import ExecutionLifecycleMode
from app.execution.mode import ModeChangeAuthorization, change_execution_mode
from app.models import ExecutionModeEvent


def _guard():
    return importlib.import_module("app.execution.promotion_guard")


def _set_mode(session, target: ExecutionLifecycleMode) -> None:
    current = importlib.import_module("app.execution.mode").get_execution_mode(session).mode
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


def test_paper_to_sandbox_requires_explicit_technical_readiness():
    guard = _guard()

    blocked = guard.evaluate_promotion(
        current=ExecutionLifecycleMode.PAPER,
        target=ExecutionLifecycleMode.SANDBOX,
        evidence=guard.PromotionEvidence(),
    )
    allowed = guard.evaluate_promotion(
        current=ExecutionLifecycleMode.PAPER,
        target=ExecutionLifecycleMode.SANDBOX,
        evidence=guard.PromotionEvidence(technical_sandbox_ready=True),
    )

    assert blocked.allowed is False
    assert blocked.blockers == ("technical sandbox readiness not verified",)
    assert allowed.allowed is True
    assert allowed.blockers == ()
    assert allowed.authorization is not None
    assert allowed.authorization.detail_json["policy_version"] == "ADR-0001"


def test_sandbox_to_canary_requires_adr_gates_and_owner_confirmation():
    guard = _guard()

    decision = guard.evaluate_promotion(
        current=ExecutionLifecycleMode.SANDBOX,
        target=ExecutionLifecycleMode.CANARY,
        evidence=guard.PromotionEvidence(),
    )

    assert decision.allowed is False
    assert decision.blockers == (
        "ADR gates not verified",
        "explicit owner confirmation missing",
    )

    allowed = guard.evaluate_promotion(
        current=ExecutionLifecycleMode.SANDBOX,
        target=ExecutionLifecycleMode.CANARY,
        evidence=guard.PromotionEvidence(
            adr_gates_passed=True,
            owner_confirmed=True,
        ),
    )
    assert allowed.allowed is True


def test_canary_to_live_requires_adr_owner_performance_and_ops_gates():
    guard = _guard()

    decision = guard.evaluate_promotion(
        current=ExecutionLifecycleMode.CANARY,
        target=ExecutionLifecycleMode.LIVE,
        evidence=guard.PromotionEvidence(owner_confirmed=True),
    )

    assert decision.allowed is False
    assert decision.blockers == (
        "ADR gates not verified",
        "performance gates not verified",
        "ops gates not verified",
    )

    allowed = guard.evaluate_promotion(
        current=ExecutionLifecycleMode.CANARY,
        target=ExecutionLifecycleMode.LIVE,
        evidence=guard.PromotionEvidence(
            adr_gates_passed=True,
            owner_confirmed=True,
            performance_gates_passed=True,
            ops_gates_passed=True,
        ),
    )
    assert allowed.allowed is True


def test_non_adjacent_upshift_is_never_authorized_even_with_all_evidence():
    guard = _guard()

    decision = guard.evaluate_promotion(
        current=ExecutionLifecycleMode.PAPER,
        target=ExecutionLifecycleMode.LIVE,
        evidence=guard.PromotionEvidence(
            technical_sandbox_ready=True,
            adr_gates_passed=True,
            owner_confirmed=True,
            performance_gates_passed=True,
            ops_gates_passed=True,
        ),
    )

    assert decision.allowed is False
    assert decision.blockers == ("stepwise promotion required",)
    assert decision.authorization is None


def test_lower_risk_transition_is_automatically_authorized_with_event_trail(session):
    guard = _guard()
    _set_mode(session, ExecutionLifecycleMode.CANARY)
    before = session.query(ExecutionModeEvent).count()

    decision = guard.preview_promotion(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
    )
    result = guard.change_mode_with_guard(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
        actor="system",
        reason="automatic risk reduction",
    )
    session.flush()

    assert decision.allowed is True
    assert decision.blockers == ()
    assert decision.authorization is not None
    assert decision.authorization.detail_json["direction"] == "lower-risk"
    assert result.mode == ExecutionLifecycleMode.SANDBOX
    event = session.query(ExecutionModeEvent).order_by(ExecutionModeEvent.occurred_at.desc()).first()
    assert event is not None
    assert session.query(ExecutionModeEvent).count() == before + 1
    assert event.from_mode == ExecutionLifecycleMode.CANARY
    assert event.to_mode == ExecutionLifecycleMode.SANDBOX
    assert event.detail_json["policy_version"] == "ADR-0001"


def test_default_server_evidence_stays_fail_closed_for_paper_to_sandbox(session):
    guard = _guard()

    decision = guard.preview_promotion(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
    )

    assert decision.allowed is False
    assert "technical sandbox readiness not verified" in decision.blockers
    assert "venue sandbox capability not verified" in decision.evidence_notes


def test_mode_api_uses_guard_and_allows_only_safe_downshift_without_future_proofs(session):
    api = importlib.import_module("app.api.v1.execution")
    _set_mode(session, ExecutionLifecycleMode.CANARY)

    preview = api.preview_execution_mode(
        request=api.ExecutionModePreviewRequest(target=ExecutionLifecycleMode.SANDBOX),
        db=session,
    )
    changed = api.change_execution_mode(
        request=api.ExecutionModeChangeRequest(
            target=ExecutionLifecycleMode.SANDBOX,
            reason="reduce risk",
        ),
        db=session,
    )

    assert preview.allowed is True
    assert preview.blockers == []
    assert changed.mode == ExecutionLifecycleMode.SANDBOX

    blocked_preview = api.preview_execution_mode(
        request=api.ExecutionModePreviewRequest(target=ExecutionLifecycleMode.CANARY),
        db=session,
    )
    assert blocked_preview.allowed is False
    assert "ADR gates not verified" in blocked_preview.blockers

    with pytest.raises(Exception) as exc_info:
        api.change_execution_mode(
            request=api.ExecutionModeChangeRequest(
                target=ExecutionLifecycleMode.CANARY,
                reason="try risk increase",
            ),
            db=session,
        )
    assert getattr(exc_info.value, "status_code", None) == 409


def test_guard_explicitly_allows_automatic_halt_action_without_promoting_mode():
    guard = _guard()

    decision = guard.authorize_halt_new_entries(reason="critical execution health")

    assert decision.allowed is True
    assert decision.action == "HALT_NEW_ENTRIES"
    assert decision.authorization.detail_json["policy_version"] == "ADR-0001"
