"""Pure fail-closed state transitions for durable execution.

This module has no I/O and no broker calls. It only defines which persisted
state changes are structurally possible; services in later SAI slices must
still prove risk, venue and reconciliation preconditions before requesting a
transition.
"""

from __future__ import annotations

from .enums import ExecutionState


class InvalidExecutionTransition(ValueError):
    pass


_ALLOWED: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.INTENT_CREATED: frozenset(
        {ExecutionState.RISK_APPROVED, ExecutionState.CANCELLED, ExecutionState.FAILED}
    ),
    ExecutionState.RISK_APPROVED: frozenset(
        {ExecutionState.READY_TO_SUBMIT, ExecutionState.CANCELLED, ExecutionState.FAILED}
    ),
    ExecutionState.READY_TO_SUBMIT: frozenset(
        {ExecutionState.SUBMITTING, ExecutionState.CANCELLED, ExecutionState.FAILED}
    ),
    ExecutionState.SUBMITTING: frozenset(
        {
            ExecutionState.ACKNOWLEDGED,
            ExecutionState.AMBIGUOUS,
            ExecutionState.REJECTED,
            ExecutionState.FAILED,
        }
    ),
    ExecutionState.AMBIGUOUS: frozenset({ExecutionState.RECONCILING}),
    ExecutionState.RECONCILING: frozenset(
        {
            ExecutionState.ACKNOWLEDGED,
            ExecutionState.READY_TO_SUBMIT,
            ExecutionState.REJECTED,
            ExecutionState.FAILED,
        }
    ),
    ExecutionState.ACKNOWLEDGED: frozenset(
        {
            ExecutionState.PARTIALLY_FILLED,
            ExecutionState.FILLED,
            ExecutionState.CANCELLED,
            ExecutionState.REJECTED,
            ExecutionState.FAILED,
        }
    ),
    ExecutionState.PARTIALLY_FILLED: frozenset(
        {
            ExecutionState.FILLED,
            ExecutionState.PROTECTION_PENDING,
            ExecutionState.EXITING,
            ExecutionState.EMERGENCY_FLATTEN,
            ExecutionState.FAILED,
        }
    ),
    ExecutionState.FILLED: frozenset(
        {
            ExecutionState.PROTECTION_PENDING,
            ExecutionState.EXITING,
            ExecutionState.EMERGENCY_FLATTEN,
            ExecutionState.FAILED,
        }
    ),
    ExecutionState.PROTECTION_PENDING: frozenset(
        {
            ExecutionState.PROTECTED,
            ExecutionState.EMERGENCY_FLATTEN,
            ExecutionState.FAILED,
        }
    ),
    ExecutionState.PROTECTED: frozenset(
        {
            ExecutionState.MANAGING,
            ExecutionState.EXITING,
            ExecutionState.EMERGENCY_FLATTEN,
            ExecutionState.FAILED,
        }
    ),
    ExecutionState.MANAGING: frozenset(
        {ExecutionState.EXITING, ExecutionState.EMERGENCY_FLATTEN, ExecutionState.FAILED}
    ),
    ExecutionState.EXITING: frozenset(
        {ExecutionState.CLOSED, ExecutionState.EMERGENCY_FLATTEN, ExecutionState.FAILED}
    ),
    ExecutionState.EMERGENCY_FLATTEN: frozenset(
        {ExecutionState.CLOSED, ExecutionState.FAILED}
    ),
    ExecutionState.CLOSED: frozenset(),
    ExecutionState.REJECTED: frozenset(),
    ExecutionState.CANCELLED: frozenset(),
    ExecutionState.FAILED: frozenset(),
}


def transition_execution_state(
    current: ExecutionState | str,
    target: ExecutionState | str,
) -> ExecutionState:
    """Validate and return ``target`` without mutating persistent state."""

    try:
        current_state = ExecutionState(current)
        target_state = ExecutionState(target)
    except ValueError as exc:
        raise InvalidExecutionTransition(f"unknown execution state: {exc}") from exc

    if target_state not in _ALLOWED[current_state]:
        raise InvalidExecutionTransition(
            f"execution transition {current_state.value} -> {target_state.value} is not allowed"
        )
    return target_state


__all__ = ["InvalidExecutionTransition", "transition_execution_state"]
