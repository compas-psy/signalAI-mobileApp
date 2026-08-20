from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.execution.enums import ExecutionState
from app.execution.manual_controls import (
    ManualTradeAction,
    ManualTradeControlRejected,
    request_manual_trade_control,
)
from app.models import (
    AuditEvent,
    ExecutionFill,
    ExecutionManagementPolicySnapshot,
    ExecutionOrder,
)
from tests.execution.test_entry_settling import _seed_intent


NOW = datetime(2026, 8, 20, 14, 15, tzinfo=UTC)


def _managed_intent(session, instrument):
    intent = _seed_intent(session, instrument, planned_quantity=Decimal("4"))
    entry = ExecutionOrder(
        intent_id=intent.id,
        client_order_id=f"e-{intent.id.hex}",
        provider_order_id="entry-provider-1",
        side="BUY",
        order_type="ENTRY",
        status="FILLED",
        quantity=Decimal("4"),
        limit_price=Decimal("90100"),
        stop_price=None,
        submitted_at=NOW,
        acknowledged_at=NOW,
    )
    session.add(entry)
    session.flush()
    session.add(
        ExecutionFill(
            intent_id=intent.id,
            order_id=entry.id,
            provider_fill_id="entry-fill-1",
            quantity=Decimal("4"),
            price=Decimal("90110"),
            fee_amount=Decimal("1"),
            fee_currency="RUB",
            filled_at=NOW,
        )
    )
    intent.state = ExecutionState.PROTECTED
    session.commit()
    snapshot = session.execute(
        select(ExecutionManagementPolicySnapshot).where(
            ExecutionManagementPolicySnapshot.intent_id == intent.id
        )
    ).scalar_one()
    intent.state = ExecutionState.MANAGING
    session.flush()
    return intent, snapshot


def test_close_is_reduce_only_and_idempotent(session, instrument):
    intent, snapshot = _managed_intent(session, instrument)

    first = request_manual_trade_control(
        session,
        intent_id=intent.id,
        action=ManualTradeAction.CLOSE,
        idempotency_key="close-1",
        owner_reason="Закрыть позицию",
        requested_quantity=None,
        requested_stop=None,
    )
    replay = request_manual_trade_control(
        session,
        intent_id=intent.id,
        action=ManualTradeAction.CLOSE,
        idempotency_key="close-1",
        owner_reason="Закрыть позицию",
        requested_quantity=None,
        requested_stop=None,
    )

    assert first.created is True
    assert replay.created is False
    assert replay.order.id == first.order.id
    assert first.order.order_type == "MANUAL_CLOSE"
    assert first.order.quantity == Decimal("4")
    assert first.order.side == "SELL"
    assert first.order.limit_price is None
    assert first.order.stop_price is None
    assert first.order.client_order_id.startswith("mc-")
    assert first.command.management_policy_snapshot_id == snapshot.id
    assert first.command.reduce_only is True


def test_reduce_requires_strictly_smaller_positive_quantity(session, instrument):
    intent, _snapshot = _managed_intent(session, instrument)

    for invalid in (Decimal("0"), Decimal("4"), Decimal("5")):
        with pytest.raises(ManualTradeControlRejected, match="strictly below"):
            request_manual_trade_control(
                session,
                intent_id=intent.id,
                action=ManualTradeAction.REDUCE,
                idempotency_key=f"bad-{invalid}",
                owner_reason="bad reduce",
                requested_quantity=invalid,
                requested_stop=None,
            )

    result = request_manual_trade_control(
        session,
        intent_id=intent.id,
        action=ManualTradeAction.REDUCE,
        idempotency_key="reduce-1",
        owner_reason="Сократить позицию",
        requested_quantity=Decimal("1"),
        requested_stop=None,
    )
    assert result.order.order_type == "MANUAL_REDUCE"
    assert result.order.quantity == Decimal("1")
    assert result.command.reduce_only is True


def test_tighten_stop_must_move_only_toward_lower_risk_for_long(session, instrument):
    intent, _snapshot = _managed_intent(session, instrument)

    with pytest.raises(ManualTradeControlRejected, match="lower risk"):
        request_manual_trade_control(
            session,
            intent_id=intent.id,
            action=ManualTradeAction.TIGHTEN_STOP,
            idempotency_key="widen-1",
            owner_reason="Нельзя расширять стоп",
            requested_quantity=None,
            requested_stop=Decimal("89000"),
        )

    result = request_manual_trade_control(
        session,
        intent_id=intent.id,
        action=ManualTradeAction.TIGHTEN_STOP,
        idempotency_key="tighten-1",
        owner_reason="Подтянуть стоп",
        requested_quantity=None,
        requested_stop=Decimal("89700"),
    )
    assert result.order.order_type == "MANUAL_TIGHTEN_STOP"
    assert result.order.quantity == Decimal("4")
    assert result.order.stop_price == Decimal("89700")
    assert result.command.reduce_only is True


def test_return_auto_creates_no_provider_order_and_is_audited(session, instrument):
    intent, snapshot = _managed_intent(session, instrument)

    result = request_manual_trade_control(
        session,
        intent_id=intent.id,
        action=ManualTradeAction.RETURN_AUTO,
        idempotency_key="auto-1",
        owner_reason="Вернуть автоматическое сопровождение",
        requested_quantity=None,
        requested_stop=None,
    )
    session.flush()

    assert result.order is None
    assert result.command.management_policy_snapshot_id == snapshot.id
    assert result.command.reduce_only is True
    events = session.execute(
        select(AuditEvent).where(
            AuditEvent.action == "manual_trade_control_requested",
            AuditEvent.subject == str(intent.id),
        )
    ).scalars().all()
    assert events
    event = events[-1]
    assert event.actor == "owner"
    assert event.after_json["action"] == "RETURN_AUTO"
    assert event.after_json["idempotency_key_sha256"]
    assert "auto-1" not in str(event.after_json)


def test_controls_fail_closed_without_frozen_management_policy(session, instrument):
    intent = _seed_intent(session, instrument, planned_quantity=Decimal("2"))
    intent.state = ExecutionState.MANAGING
    session.flush()

    with pytest.raises(ManualTradeControlRejected, match="management policy"):
        request_manual_trade_control(
            session,
            intent_id=intent.id,
            action=ManualTradeAction.CLOSE,
            idempotency_key="no-policy",
            owner_reason="close",
            requested_quantity=None,
            requested_stop=None,
        )
