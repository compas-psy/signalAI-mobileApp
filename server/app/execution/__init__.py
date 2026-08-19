"""Durable server execution domain.

SAI-024 defines persistence and state vocabulary only. Later R2 slices add
intent durability, worker orchestration and fail-closed control without enabling
real broker execution by themselves.
"""

from .domain import InvalidExecutionTransition, transition_execution_state
from .enums import ExecutionKillSwitchLevel, ExecutionLifecycleMode, ExecutionState

__all__ = [
    "ExecutionKillSwitchLevel",
    "ExecutionLifecycleMode",
    "ExecutionState",
    "InvalidExecutionTransition",
    "transition_execution_state",
]
