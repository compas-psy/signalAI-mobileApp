"""Execution-domain vocabulary for the durable server execution core.

Defining these enums does not enable broker execution or change the current
runtime mode. SAI-030 owns server-side mode activation and promotion guards.
"""

from __future__ import annotations

from enum import StrEnum


class ExecutionState(StrEnum):
    INTENT_CREATED = "INTENT_CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    PROTECTION_PENDING = "PROTECTION_PENDING"
    PROTECTED = "PROTECTED"
    MANAGING = "MANAGING"
    EXITING = "EXITING"
    CLOSED = "CLOSED"

    AMBIGUOUS = "AMBIGUOUS"
    RECONCILING = "RECONCILING"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    EMERGENCY_FLATTEN = "EMERGENCY_FLATTEN"


class ExecutionLifecycleMode(StrEnum):
    """Persistence vocabulary only; mode ownership is implemented in SAI-030."""

    PAPER = "PAPER"
    SANDBOX = "SANDBOX"
    CANARY = "CANARY"
    LIVE = "LIVE"


__all__ = ["ExecutionLifecycleMode", "ExecutionState"]
