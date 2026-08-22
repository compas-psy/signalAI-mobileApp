from __future__ import annotations

from decimal import Decimal

from app.execution.enums import ExecutionLifecycleMode
from app.execution.mode import (
    ModeChangeAuthorization,
    change_execution_mode,
    get_execution_mode,
)
from app.execution.promotion_guard import PromotionEvidence


def _set_canary(session) -> None:
    change_execution_mode(
        session,
        target=ExecutionLifecycleMode.CANARY,
        actor="test",
        reason="owner-step-up regression setup",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test-only setup",
            detail_json={"test_only": True},
        ),
    )
    session.flush()


def test_boolean_only_live_confirmation_is_blocked_before_mode_change(session) -> None:
    """A stolen device bearer plus owner_confirmed=true is never owner authority."""

    from app.execution import live_activation

    _set_canary(session)
    context = live_activation.LiveActivationContext(
        venue="LIGHTER",
        account="canary-main",
        capital_rub=Decimal("10000"),
        hard_caps={"max_risk_per_trade": "0.0025"},
        config_hash="a" * 64,
        paper_only=False,
    )

    def ready_evidence(*_args, **_kwargs) -> PromotionEvidence:
        return PromotionEvidence(
            adr_gates_passed=True,
            performance_gates_passed=True,
            ops_gates_passed=True,
        )

    preview = live_activation.create_live_activation_preview(
        session,
        context_provider=lambda *_: context,
        evidence_provider=ready_evidence,
    )
    assert preview.blockers == ("explicit owner confirmation missing",)

    result = live_activation.confirm_live_activation(
        session,
        preview_hash=preview.preview_hash,
        idempotency_key="stolen-device-replay-key",
        owner_confirmed=True,
        context_provider=lambda *_: context,
        evidence_provider=ready_evidence,
    )

    assert result.status == "BLOCKED"
    assert result.mode == ExecutionLifecycleMode.CANARY
    assert "OWNER_STEP_UP_NOT_IMPLEMENTED" in result.blockers
    assert get_execution_mode(session).mode == ExecutionLifecycleMode.CANARY
