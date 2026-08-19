from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.execution.intent_service import (
    ExecutionIntentGate,
    ExecutionIntentRejected,
    ExecutionIntentRequest,
    create_execution_intent,
)
from app.execution.mode import ModeChangeAuthorization, change_execution_mode
from app.execution.enums import ExecutionLifecycleMode
from app.execution.risk_override import (
    ExecutionRiskOverrideRejected,
    RiskOverrideAuthorization,
    RiskOverrideRequest,
    create_execution_risk_override,
)
from app.models import AuditEvent, ExecutionRiskOverride, RiskSnapshot, TradeIdea
from tests.conftest import idea_kwargs


def _seed(session, instrument, now, *, blocked: bool = False):
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            status="TRIGGERED",
            quality_status="PASS",
            risk_pct=Decimal("0.005"),
            quantity=Decimal("1"),
        )
    )
    risk = RiskSnapshot(
        risk_equity=Decimal("100000"),
        entries_blocked=blocked,
        halted=False,
    )
    session.add_all([idea, risk])
    session.flush()
    return idea, risk


def _authorization() -> RiskOverrideAuthorization:
    return RiskOverrideAuthorization(
        allowed=True,
        actor="risk-policy",
        reason="bounded owner RISK_ON preview",
        hard_cap_risk_pct=Decimal("0.01"),
        hard_cap_leverage=Decimal("3"),
        detail_json={"policy_version": "risk-preview-v1"},
    )


def _request(idea, risk, **overrides) -> RiskOverrideRequest:
    values = dict(
        idea_id=idea.id,
        risk_snapshot_id=risk.id,
        preset="RISK_ON",
        venue="MOEX",
        account="sandbox-main",
        effective_risk_pct=Decimal("0.008"),
        effective_quantity=Decimal("2"),
        effective_leverage=Decimal("2"),
        idempotency_key="risk-on-1",
        owner_confirmed=True,
        reason="owner tapped Рискнуть",
    )
    values.update(overrides)
    return RiskOverrideRequest(**values)


def _intent_gate() -> ExecutionIntentGate:
    return ExecutionIntentGate(
        owner_approved=True,
        risk_snapshot_verified=True,
        mode_allows_intent=True,
        kill_switch_clear=True,
        venue_capability_verified=True,
    )


def test_risk_override_requires_owner_and_cannot_bypass_blocked_snapshot(
    session, instrument, now
):
    idea, risk = _seed(session, instrument, now)

    with pytest.raises(ExecutionRiskOverrideRejected, match="owner confirmation"):
        create_execution_risk_override(
            session,
            request=_request(idea, risk, owner_confirmed=False),
            authorization=_authorization(),
        )

    risk.entries_blocked = True
    session.flush()
    with pytest.raises(ExecutionRiskOverrideRejected, match="blocks new entries"):
        create_execution_risk_override(
            session,
            request=_request(idea, risk, idempotency_key="risk-on-blocked"),
            authorization=_authorization(),
        )


def test_risk_override_is_bounded_by_authoritative_risk_and_leverage_caps(
    session, instrument, now
):
    idea, risk = _seed(session, instrument, now)

    with pytest.raises(ExecutionRiskOverrideRejected, match="hard risk cap"):
        create_execution_risk_override(
            session,
            request=_request(
                idea,
                risk,
                effective_risk_pct=Decimal("0.011"),
                idempotency_key="risk-too-high",
            ),
            authorization=_authorization(),
        )

    with pytest.raises(ExecutionRiskOverrideRejected, match="hard leverage cap"):
        create_execution_risk_override(
            session,
            request=_request(
                idea,
                risk,
                effective_leverage=Decimal("4"),
                idempotency_key="leverage-too-high",
            ),
            authorization=_authorization(),
        )


def test_risk_override_is_immutable_idempotent_and_audited(session, instrument, now):
    idea, risk = _seed(session, instrument, now)
    request = _request(idea, risk)

    first = create_execution_risk_override(
        session,
        request=request,
        authorization=_authorization(),
    )
    second = create_execution_risk_override(
        session,
        request=request,
        authorization=_authorization(),
    )
    session.flush()

    assert first.created is True
    assert second.created is False
    assert second.override.id == first.override.id
    override = first.override
    assert override.preset == "RISK_ON"
    assert override.base_risk_pct == Decimal("0.005")
    assert override.effective_risk_pct == Decimal("0.008")
    assert override.base_quantity == Decimal("1")
    assert override.effective_quantity == Decimal("2")
    assert override.effective_leverage == Decimal("2")
    assert override.hard_cap_risk_pct == Decimal("0.01")
    assert override.hard_cap_leverage == Decimal("3")
    assert override.execution_mode_snapshot == ExecutionLifecycleMode.PAPER
    assert len(override.preview_hash) == 64

    audits = session.execute(
        select(AuditEvent).where(AuditEvent.action == "execution_risk_override_created")
    ).scalars().all()
    assert len(audits) == 1
    assert audits[0].actor == "owner"
    assert audits[0].subject == str(idea.id)

    triggers = session.execute(
        text(
            """
            SELECT t.tgname
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            WHERE NOT t.tgisinternal
              AND c.relname = 'execution_risk_overrides'
              AND t.tgname = 'execution_risk_overrides_append_only'
            """
        )
    ).scalars().all()
    assert triggers == ["execution_risk_overrides_append_only"]

    with pytest.raises(ExecutionRiskOverrideRejected, match="idempotency key"):
        create_execution_risk_override(
            session,
            request=_request(
                idea,
                risk,
                effective_risk_pct=Decimal("0.009"),
            ),
            authorization=_authorization(),
        )


def test_intent_must_match_the_exact_risk_override_scope_and_quantity(
    session, instrument, now
):
    idea, risk = _seed(session, instrument, now)
    override = create_execution_risk_override(
        session,
        request=_request(idea, risk),
        authorization=_authorization(),
    ).override

    with pytest.raises(ExecutionIntentRejected, match="override quantity"):
        create_execution_intent(
            session,
            request=ExecutionIntentRequest(
                idea_id=idea.id,
                instrument_id=idea.instrument_id,
                strategy_version=idea.strategy_version,
                risk_policy_snapshot_id=risk.id,
                risk_override_id=override.id,
                venue="MOEX",
                account="sandbox-main",
                planned_quantity=Decimal("3"),
                planned_entry_price=idea.entry_reference,
                planned_stop_price=idea.stop,
            ),
            gate=_intent_gate(),
        )

    with pytest.raises(ExecutionIntentRejected, match="override venue/account"):
        create_execution_intent(
            session,
            request=ExecutionIntentRequest(
                idea_id=idea.id,
                instrument_id=idea.instrument_id,
                strategy_version=idea.strategy_version,
                risk_policy_snapshot_id=risk.id,
                risk_override_id=override.id,
                venue="BYBIT",
                account="sandbox-main",
                planned_quantity=Decimal("2"),
                planned_entry_price=idea.entry_reference,
                planned_stop_price=idea.stop,
            ),
            gate=_intent_gate(),
        )

    creation = create_execution_intent(
        session,
        request=ExecutionIntentRequest(
            idea_id=idea.id,
            instrument_id=idea.instrument_id,
            strategy_version=idea.strategy_version,
            risk_policy_snapshot_id=risk.id,
            risk_override_id=override.id,
            venue="MOEX",
            account="sandbox-main",
            planned_quantity=Decimal("2"),
            planned_entry_price=idea.entry_reference,
            planned_stop_price=idea.stop,
        ),
        gate=_intent_gate(),
    )
    assert creation.intent.risk_override_id == override.id
    assert creation.intent.planned_quantity == Decimal("2")


def test_risk_override_cannot_be_replayed_after_execution_mode_change(
    session, instrument, now
):
    idea, risk = _seed(session, instrument, now)
    override = create_execution_risk_override(
        session,
        request=_request(idea, risk),
        authorization=_authorization(),
    ).override
    assert override.execution_mode_snapshot == ExecutionLifecycleMode.PAPER

    change_execution_mode(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
        actor="owner",
        reason="test promotion",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test-only proof",
        ),
    )

    with pytest.raises(ExecutionIntentRejected, match="override execution mode"):
        create_execution_intent(
            session,
            request=ExecutionIntentRequest(
                idea_id=idea.id,
                instrument_id=idea.instrument_id,
                strategy_version=idea.strategy_version,
                risk_policy_snapshot_id=risk.id,
                risk_override_id=override.id,
                venue="MOEX",
                account="sandbox-main",
                planned_quantity=Decimal("2"),
                planned_entry_price=idea.entry_reference,
                planned_stop_price=idea.stop,
            ),
            gate=_intent_gate(),
        )
