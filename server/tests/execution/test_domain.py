from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import Numeric

from app.execution.domain import InvalidExecutionTransition, transition_execution_state
from app.execution.enums import ExecutionLifecycleMode, ExecutionState
from app.models import (
    ExecutionFill,
    ExecutionIntent,
    ExecutionModeEvent,
    ExecutionModeState,
    ExecutionOrder,
    ExecutionProtection,
    ExecutionReconciliationEvent,
)


EXPECTED_STATES = {
    "INTENT_CREATED",
    "RISK_APPROVED",
    "READY_TO_SUBMIT",
    "SUBMITTING",
    "ACKNOWLEDGED",
    "PARTIALLY_FILLED",
    "FILLED",
    "PROTECTION_PENDING",
    "PROTECTED",
    "MANAGING",
    "EXITING",
    "CLOSED",
    "AMBIGUOUS",
    "RECONCILING",
    "REJECTED",
    "CANCELLED",
    "FAILED",
    "EMERGENCY_FLATTEN",
}


def test_execution_state_contract_matches_backlog_exactly():
    assert {state.value for state in ExecutionState} == EXPECTED_STATES


def test_lifecycle_mode_schema_is_ready_but_does_not_enable_live():
    assert [mode.value for mode in ExecutionLifecycleMode] == [
        "PAPER",
        "SANDBOX",
        "CANARY",
        "LIVE",
    ]
    # SAI-024 defines persistence vocabulary only. No default or transition here
    # may silently promote a runtime into a live mode.
    assert ExecutionLifecycleMode.PAPER.value == "PAPER"


def test_happy_path_state_machine_is_forward_only():
    state = ExecutionState.INTENT_CREATED
    for expected in (
        ExecutionState.RISK_APPROVED,
        ExecutionState.READY_TO_SUBMIT,
        ExecutionState.SUBMITTING,
        ExecutionState.ACKNOWLEDGED,
        ExecutionState.PARTIALLY_FILLED,
        ExecutionState.FILLED,
        ExecutionState.PROTECTION_PENDING,
        ExecutionState.PROTECTED,
        ExecutionState.MANAGING,
        ExecutionState.EXITING,
        ExecutionState.CLOSED,
    ):
        state = transition_execution_state(state, expected)
        assert state is expected

    with pytest.raises(InvalidExecutionTransition):
        transition_execution_state(ExecutionState.CLOSED, ExecutionState.MANAGING)


def test_ambiguous_submit_must_reconcile_before_returning_to_normal_path():
    assert transition_execution_state(
        ExecutionState.SUBMITTING, ExecutionState.AMBIGUOUS
    ) is ExecutionState.AMBIGUOUS
    assert transition_execution_state(
        ExecutionState.AMBIGUOUS, ExecutionState.RECONCILING
    ) is ExecutionState.RECONCILING
    assert transition_execution_state(
        ExecutionState.RECONCILING, ExecutionState.ACKNOWLEDGED
    ) is ExecutionState.ACKNOWLEDGED
    assert transition_execution_state(
        ExecutionState.RECONCILING, ExecutionState.READY_TO_SUBMIT
    ) is ExecutionState.READY_TO_SUBMIT

    with pytest.raises(InvalidExecutionTransition):
        transition_execution_state(
            ExecutionState.AMBIGUOUS, ExecutionState.READY_TO_SUBMIT
        )


def test_emergency_flatten_is_only_reachable_after_position_exposure():
    for exposed in (
        ExecutionState.PARTIALLY_FILLED,
        ExecutionState.FILLED,
        ExecutionState.PROTECTION_PENDING,
        ExecutionState.PROTECTED,
        ExecutionState.MANAGING,
        ExecutionState.EXITING,
    ):
        assert transition_execution_state(
            exposed, ExecutionState.EMERGENCY_FLATTEN
        ) is ExecutionState.EMERGENCY_FLATTEN

    with pytest.raises(InvalidExecutionTransition):
        transition_execution_state(
            ExecutionState.READY_TO_SUBMIT, ExecutionState.EMERGENCY_FLATTEN
        )


def test_execution_models_define_all_required_tables():
    assert {
        ExecutionModeState.__tablename__,
        ExecutionModeEvent.__tablename__,
        ExecutionIntent.__tablename__,
        ExecutionOrder.__tablename__,
        ExecutionFill.__tablename__,
        ExecutionProtection.__tablename__,
        ExecutionReconciliationEvent.__tablename__,
    } == {
        "execution_mode_state",
        "execution_mode_events",
        "execution_intents",
        "execution_orders",
        "execution_fills",
        "execution_protections",
        "execution_reconciliation_events",
    }


def test_money_price_and_quantity_columns_are_exact_numeric_types():
    exact_columns = (
        ExecutionIntent.__table__.c.planned_quantity,
        ExecutionIntent.__table__.c.planned_entry_price,
        ExecutionIntent.__table__.c.planned_stop_price,
        ExecutionOrder.__table__.c.quantity,
        ExecutionOrder.__table__.c.limit_price,
        ExecutionOrder.__table__.c.stop_price,
        ExecutionFill.__table__.c.quantity,
        ExecutionFill.__table__.c.price,
        ExecutionFill.__table__.c.fee_amount,
        ExecutionProtection.__table__.c.quantity,
        ExecutionProtection.__table__.c.stop_price,
    )
    assert all(isinstance(column.type, Numeric) for column in exact_columns)


def test_decimal_payloads_are_not_coerced_to_float_before_persistence():
    intent = ExecutionIntent(
        idea_id="00000000-0000-0000-0000-000000000001",
        instrument_id="CRYPTO:BTCUSDT",
        strategy_version="legacy_control_v1",
        risk_policy_snapshot_id="00000000-0000-0000-0000-000000000002",
        venue="BYBIT",
        account="paper",
        state=ExecutionState.INTENT_CREATED,
        planned_quantity=Decimal("0.001234567890"),
        planned_entry_price=Decimal("12345.678901234567"),
        planned_stop_price=Decimal("12000.000000000001"),
    )
    assert isinstance(intent.planned_quantity, Decimal)
    assert isinstance(intent.planned_entry_price, Decimal)
    assert isinstance(intent.planned_stop_price, Decimal)
