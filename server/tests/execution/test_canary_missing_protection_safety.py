from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.execution.enums import ExecutionKillSwitchLevel, ExecutionLifecycleMode
from app.execution.intent_service import (
    ExecutionIntentGate,
    ExecutionIntentRequest,
    create_execution_intent,
)
from app.execution.kill_switch import (
    get_execution_kill_switch_level,
    set_execution_kill_switch,
)
from app.execution.mode import ModeChangeAuthorization, change_execution_mode
from app.models import ExecutionFill, ExecutionOrder, ExecutionProtection
from app.models.ideas import TradeIdea
from app.models.risk import RiskSnapshot
from tests.conftest import idea_kwargs


BASE = datetime(2026, 8, 23, 0, 0, tzinfo=UTC)


def _set_mode(session, target: ExecutionLifecycleMode) -> None:
    change_execution_mode(
        session,
        target=target,
        actor="test",
        reason="missing-protection safety setup",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test setup authorization",
            detail_json={"test_only": True},
        ),
    )
    session.flush()


def _intent(session, instrument, *, mode: ExecutionLifecycleMode = ExecutionLifecycleMode.CANARY):
    _set_mode(session, mode)
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            BASE,
            status="TRIGGERED",
            quality_status="PASS",
            score=Decimal("82"),
        )
    )
    risk = RiskSnapshot(risk_equity=Decimal("100000"))
    session.add_all([idea, risk])
    session.flush()
    result = create_execution_intent(
        session,
        request=ExecutionIntentRequest(
            idea_id=idea.id,
            instrument_id=idea.instrument_id,
            strategy_version=idea.strategy_version,
            risk_policy_snapshot_id=risk.id,
            risk_override_id=None,
            venue="LIGHTER",
            account="canary-main",
            planned_quantity=Decimal("1"),
            planned_entry_price=Decimal("100"),
            planned_stop_price=Decimal("95"),
        ),
        gate=ExecutionIntentGate(
            owner_approved=True,
            risk_snapshot_verified=True,
            mode_allows_intent=True,
            kill_switch_clear=True,
            venue_capability_verified=True,
        ),
    )
    session.flush()
    return result.intent


def _fill(session, intent, *, filled_at: datetime = BASE):
    order = ExecutionOrder(
        intent_id=intent.id,
        client_order_id=f"entry-{intent.id.hex}",
        provider_order_id=f"provider-{intent.id.hex}",
        side="BUY",
        order_type="ENTRY",
        status="FILLED",
        quantity=Decimal("1"),
        limit_price=Decimal("100"),
        stop_price=None,
        submitted_at=filled_at - timedelta(seconds=1),
        acknowledged_at=filled_at - timedelta(milliseconds=500),
    )
    session.add(order)
    session.flush()
    session.add(
        ExecutionFill(
            intent_id=intent.id,
            order_id=order.id,
            provider_fill_id=f"fill-{intent.id.hex}",
            quantity=Decimal("1"),
            price=Decimal("100"),
            fee_amount=Decimal("0"),
            fee_currency="USDC",
            filled_at=filled_at,
        )
    )
    session.flush()
    return order


def _active_protection(session, intent, order, *, armed_at: datetime, status: str = "ACTIVE"):
    session.add(
        ExecutionProtection(
            intent_id=intent.id,
            order_id=order.id,
            protection_type="STOP",
            status=status,
            provider_order_id=f"stop-{intent.id.hex}" if status == "ACTIVE" else None,
            quantity=Decimal("1"),
            stop_price=Decimal("95"),
            armed_at=armed_at,
            last_reconciled_at=armed_at,
        )
    )
    session.flush()


def test_canary_lighter_fill_without_active_protection_after_sla_halts(session, instrument):
    from app.execution.automatic_safety import automatic_halt_if_canary_missing_protection

    intent = _intent(session, instrument)
    _fill(session, intent)

    result = automatic_halt_if_canary_missing_protection(
        session,
        intent_id=intent.id,
        as_of=BASE + timedelta(seconds=31),
    )

    assert result.trigger == "MISSING_PROTECTION"
    assert result.naked_ms == 31000
    assert result.protection_sla_ms == 30000
    assert result.halt is not None and result.halt.changed is True
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.HALT_NEW_ENTRIES


def test_before_existing_protection_sla_does_not_halt(session, instrument):
    from app.execution.automatic_safety import automatic_halt_if_canary_missing_protection

    intent = _intent(session, instrument)
    _fill(session, intent)

    result = automatic_halt_if_canary_missing_protection(
        session,
        intent_id=intent.id,
        as_of=BASE + timedelta(seconds=29),
    )

    assert result.trigger is None
    assert result.naked_ms == 29000
    assert result.protection_sla_ms == 30000
    assert result.halt is None
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.CLEAR


def test_current_active_protection_prevents_rehalt_even_if_armed_late(session, instrument):
    from app.execution.automatic_safety import automatic_halt_if_canary_missing_protection

    intent = _intent(session, instrument)
    order = _fill(session, intent)
    _active_protection(
        session,
        intent,
        order,
        armed_at=BASE + timedelta(seconds=35),
    )

    result = automatic_halt_if_canary_missing_protection(
        session,
        intent_id=intent.id,
        as_of=BASE + timedelta(seconds=40),
    )

    assert result.trigger is None
    assert result.naked_ms is None
    assert result.halt is None
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.CLEAR


def test_failed_protection_is_still_missing_after_sla(session, instrument):
    from app.execution.automatic_safety import automatic_halt_if_canary_missing_protection

    intent = _intent(session, instrument)
    order = _fill(session, intent)
    _active_protection(
        session,
        intent,
        order,
        armed_at=BASE + timedelta(seconds=10),
        status="FAILED",
    )

    result = automatic_halt_if_canary_missing_protection(
        session,
        intent_id=intent.id,
        as_of=BASE + timedelta(seconds=31),
    )

    assert result.trigger == "MISSING_PROTECTION"
    assert result.halt is not None
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.HALT_NEW_ENTRIES


def test_old_sandbox_intent_cannot_halt_current_canary(session, instrument):
    from app.execution.automatic_safety import automatic_halt_if_canary_missing_protection

    intent = _intent(session, instrument, mode=ExecutionLifecycleMode.SANDBOX)
    _fill(session, intent)
    _set_mode(session, ExecutionLifecycleMode.CANARY)

    result = automatic_halt_if_canary_missing_protection(
        session,
        intent_id=intent.id,
        as_of=BASE + timedelta(seconds=31),
    )

    assert result.trigger is None
    assert result.halt is None
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.CLEAR


def test_canary_intent_without_fill_does_not_halt(session, instrument):
    from app.execution.automatic_safety import automatic_halt_if_canary_missing_protection

    intent = _intent(session, instrument)

    result = automatic_halt_if_canary_missing_protection(
        session,
        intent_id=intent.id,
        as_of=BASE + timedelta(seconds=60),
    )

    assert result.trigger is None
    assert result.naked_ms is None
    assert result.halt is None
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.CLEAR


def test_missing_protection_never_weakens_stronger_owner_switch(session, instrument):
    from app.execution.automatic_safety import automatic_halt_if_canary_missing_protection

    intent = _intent(session, instrument)
    _fill(session, intent)
    set_execution_kill_switch(
        session,
        level=ExecutionKillSwitchLevel.CANCEL_PENDING_ENTRIES,
        actor="owner",
        reason="owner escalated before protection check",
    )

    result = automatic_halt_if_canary_missing_protection(
        session,
        intent_id=intent.id,
        as_of=BASE + timedelta(seconds=31),
    )

    assert result.trigger == "MISSING_PROTECTION"
    assert result.halt is not None and result.halt.changed is False
    assert result.halt.before is ExecutionKillSwitchLevel.CANCEL_PENDING_ENTRIES
    assert result.halt.after is ExecutionKillSwitchLevel.CANCEL_PENDING_ENTRIES
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.CANCEL_PENDING_ENTRIES
