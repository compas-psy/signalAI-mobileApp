"""Durable server execution domain.

SAI-024 defines persistence and state vocabulary only. It does not submit
orders or replace the current production paper/sandbox execution path.
"""

from .domain import InvalidExecutionTransition, transition_execution_state
from .enums import ExecutionLifecycleMode, ExecutionState

__all__ = [
    "ExecutionLifecycleMode",
    "ExecutionState",
    "InvalidExecutionTransition",
    "transition_execution_state",
]
