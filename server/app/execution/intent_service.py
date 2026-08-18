"""Durable, fail-closed creation of execution intents (SAI-025 / B5.2).

This module creates *intent records only*. It has no venue client, no worker and
no order-submit capability. Concrete mode ownership, venue capability checks
and risk-preview providers are wired by later SAI slices; until then callers
must supply an explicit proof envelope and all gates fail closed.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..models.execution import ExecutionIntent
from ..models.ideas import TradeIdea
from ..models.risk import RiskSnapshot
from .enums import ExecutionState


class ExecutionIntentRejected(ValueError):
    """A durable intent was not created because a prerequisite was not proven."""


@dataclass(frozen=True)
class ExecutionIntentGate:
    owner_approved: bool
    risk_snapshot_verified: bool
    mode_allows_intent: bool
    kill_switch_clear: bool
    venue_capability_verified: bool


@dataclass(frozen=True)
class ExecutionIntentRequest:
    idea_id: uuid.UUID
    instrument_id: str
    strategy_version: str
    risk_policy_snapshot_id: uuid.UUID
    risk_override_id: uuid.UUID | None
    venue: str
    account: str
    planned_quantity: Decimal
    planned_entry_price: Decimal
    planned_stop_price: Decimal


@dataclass(frozen=True)
class ExecutionIntentCreation:
    intent: ExecutionIntent
    created: bool


_REQUIRED_GATES = (
    "owner_approved",
    "risk_snapshot_verified",
    "mode_allows_intent",
    "kill_switch_clear",
    "venue_capability_verified",
)


def execution_intent_identity_hash(request: ExecutionIntentRequest) -> str:
    """Content address the stable B5.2 execution identity.

    Plan values are intentionally excluded. A retry of the same approved
    decision must resolve to the same intent; if a caller presents a different
    plan for that identity, creation fails instead of silently mutating it.
    """

    payload = {
        "account": request.account,
        "idea_id": str(request.idea_id),
        "risk_override_id": (
            str(request.risk_override_id) if request.risk_override_id is not None else None
        ),
        "risk_policy_snapshot_id": str(request.risk_policy_snapshot_id),
        "strategy_version": request.strategy_version,
        "venue": request.venue,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_gate(gate: ExecutionIntentGate) -> None:
    for field in _REQUIRED_GATES:
        if not getattr(gate, field):
            raise ExecutionIntentRejected(f"required execution gate is false: {field}")


def _validate_request(db: Session, request: ExecutionIntentRequest) -> None:
    if not request.venue.strip():
        raise ExecutionIntentRejected("venue must be non-empty")
    if not request.account.strip():
        raise ExecutionIntentRejected("account must be non-empty")
    if request.planned_quantity <= 0:
        raise ExecutionIntentRejected("planned_quantity must be positive")
    if request.planned_entry_price <= 0 or request.planned_stop_price <= 0:
        raise ExecutionIntentRejected("planned prices must be positive")

    idea = db.get(TradeIdea, request.idea_id)
    if idea is None:
        raise ExecutionIntentRejected("idea_id does not exist")
    if idea.instrument_id != request.instrument_id:
        raise ExecutionIntentRejected("instrument_id does not match idea")
    if idea.strategy_version != request.strategy_version:
        raise ExecutionIntentRejected("strategy_version does not match idea provenance")

    risk = db.get(RiskSnapshot, request.risk_policy_snapshot_id)
    if risk is None:
        raise ExecutionIntentRejected("risk_policy_snapshot_id does not exist")
    if risk.entries_blocked or risk.halted:
        raise ExecutionIntentRejected("risk snapshot blocks new entries")


def _same_plan(intent: ExecutionIntent, request: ExecutionIntentRequest) -> bool:
    return (
        intent.instrument_id == request.instrument_id
        and intent.planned_quantity == request.planned_quantity
        and intent.planned_entry_price == request.planned_entry_price
        and intent.planned_stop_price == request.planned_stop_price
    )


def create_execution_intent(
    db: Session,
    *,
    request: ExecutionIntentRequest,
    gate: ExecutionIntentGate,
) -> ExecutionIntentCreation:
    """Persist one intent per stable execution identity.

    PostgreSQL ``ON CONFLICT DO NOTHING`` makes duplicate retry/concurrency
    safe without turning a timeout into a second money-bearing intent.
    """

    _validate_gate(gate)
    _validate_request(db, request)
    identity_hash = execution_intent_identity_hash(request)

    stmt = (
        insert(ExecutionIntent)
        .values(
            identity_hash=identity_hash,
            idea_id=request.idea_id,
            instrument_id=request.instrument_id,
            strategy_version=request.strategy_version,
            risk_policy_snapshot_id=request.risk_policy_snapshot_id,
            risk_override_id=request.risk_override_id,
            venue=request.venue,
            account=request.account,
            state=ExecutionState.INTENT_CREATED.value,
            planned_quantity=request.planned_quantity,
            planned_entry_price=request.planned_entry_price,
            planned_stop_price=request.planned_stop_price,
        )
        .on_conflict_do_nothing(index_elements=[ExecutionIntent.identity_hash])
        .returning(ExecutionIntent.id)
    )
    created_id = db.execute(stmt).scalar_one_or_none()
    created = created_id is not None

    if created:
        intent = db.get(ExecutionIntent, created_id)
    else:
        intent = db.execute(
            select(ExecutionIntent).where(ExecutionIntent.identity_hash == identity_hash)
        ).scalar_one_or_none()

    if intent is None:  # Defensive: conflict target must always resolve.
        raise RuntimeError("execution intent conflict did not resolve to a durable row")
    if not _same_plan(intent, request):
        raise ExecutionIntentRejected(
            "stable execution identity already exists with a different plan"
        )
    return ExecutionIntentCreation(intent=intent, created=created)


__all__ = [
    "ExecutionIntentCreation",
    "ExecutionIntentGate",
    "ExecutionIntentRejected",
    "ExecutionIntentRequest",
    "create_execution_intent",
    "execution_intent_identity_hash",
]
