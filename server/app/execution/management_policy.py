"""Freeze the immutable management contract for an opened trade (SAI-049)."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.execution import ExecutionIntent, ExecutionRiskOverride
from ..models.ideas import TradeIdea
from ..models.management import ExecutionManagementPolicySnapshot
from ..models.risk import RiskSnapshot
from .enums import ExecutionState


class ManagementPolicySnapshotRejected(ValueError):
    """The opened trade cannot obtain or mutate its frozen management policy."""


@dataclass(frozen=True)
class ManagementPolicySnapshotFreeze:
    snapshot: ExecutionManagementPolicySnapshot
    created: bool


def _fixed(value: Decimal | None, places: int) -> str | None:
    if value is None:
        return None
    quantum = Decimal(1).scaleb(-places)
    return format(Decimal(value).quantize(quantum), "f")


def _risk_policy(risk: RiskSnapshot) -> dict:
    return {
        "risk_equity": _fixed(risk.risk_equity, 8),
        "open_risk": _fixed(risk.open_risk, 8),
        "binding_limit": risk.binding_limit,
        "entries_blocked": bool(risk.entries_blocked),
        "halted": bool(risk.halted),
        "cluster_risk": dict(risk.cluster_risk_json or {}),
        "detail": dict(risk.detail_json or {}),
    }


def _manual_override(override: ExecutionRiskOverride | None) -> dict:
    if override is None:
        return {
            "preset": "AUTO",
            "risk_override_id": None,
        }
    return {
        "risk_override_id": str(override.id),
        "preset": override.preset,
        "base_risk_pct": _fixed(override.base_risk_pct, 8),
        "effective_risk_pct": _fixed(override.effective_risk_pct, 8),
        "hard_cap_risk_pct": _fixed(override.hard_cap_risk_pct, 8),
        "base_quantity": _fixed(override.base_quantity, 12),
        "effective_quantity": _fixed(override.effective_quantity, 12),
        "effective_leverage": _fixed(override.effective_leverage, 8),
        "hard_cap_leverage": _fixed(override.hard_cap_leverage, 8),
        "actor": override.actor,
        "reason": override.reason,
        "detail": dict(override.detail_json or {}),
    }


def _canonical_hash(payload: dict) -> str:
    try:
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as exc:
        raise ManagementPolicySnapshotRejected(
            "management policy must be JSON-serializable"
        ) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_mapping(name: str, value: dict) -> dict:
    if not isinstance(value, dict) or not value:
        raise ManagementPolicySnapshotRejected(f"{name} must be a non-empty object")
    # Make a detached JSON-safe value so later caller mutations cannot mutate
    # the ORM JSON object that is about to become the immutable audit fact.
    try:
        return json.loads(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        )
    except (TypeError, ValueError) as exc:
        raise ManagementPolicySnapshotRejected(
            f"{name} must be JSON-serializable"
        ) from exc


def freeze_management_policy_snapshot(
    db: Session,
    *,
    intent_id: uuid.UUID,
    exit_profile: dict,
    venue_rules: dict,
) -> ManagementPolicySnapshotFreeze:
    """Persist one content-addressed immutable management policy per intent.

    Creation is allowed only once the entry is fully settled and protection has
    been proven by the execution state machine. Exact retries are idempotent;
    any later optimizer/config attempt that would change the frozen policy is
    rejected rather than silently changing an open position.
    """

    intent = db.get(ExecutionIntent, intent_id)
    if intent is None:
        raise ManagementPolicySnapshotRejected("execution intent does not exist")

    exit_facts = _validate_mapping("exit_profile", exit_profile)
    venue_facts = _validate_mapping("venue_rules", venue_rules)

    idea = db.get(TradeIdea, intent.idea_id)
    risk = db.get(RiskSnapshot, intent.risk_policy_snapshot_id)
    if idea is None or risk is None:
        raise ManagementPolicySnapshotRejected(
            "intent provenance is incomplete: idea/risk snapshot missing"
        )
    if idea.strategy_version != intent.strategy_version:
        raise ManagementPolicySnapshotRejected(
            "intent strategy version no longer matches immutable idea provenance"
        )

    override = None
    if intent.risk_override_id is not None:
        override = db.get(ExecutionRiskOverride, intent.risk_override_id)
        if override is None:
            raise ManagementPolicySnapshotRejected("intent risk override is missing")
        if (
            override.idea_id != intent.idea_id
            or override.risk_snapshot_id != intent.risk_policy_snapshot_id
            or override.venue != intent.venue
            or override.account != intent.account
        ):
            raise ManagementPolicySnapshotRejected(
                "intent risk override no longer matches execution scope"
            )

    risk_facts = _risk_policy(risk)
    override_facts = _manual_override(override)
    payload = {
        "intent_id": str(intent.id),
        "strategy_version": intent.strategy_version,
        "risk_policy_snapshot_id": str(intent.risk_policy_snapshot_id),
        "risk_override_id": (
            str(intent.risk_override_id) if intent.risk_override_id is not None else None
        ),
        "risk_policy": risk_facts,
        "manual_override": override_facts,
        "exit_profile": exit_facts,
        "venue_rules": venue_facts,
    }
    content_hash = _canonical_hash(payload)

    existing = db.execute(
        select(ExecutionManagementPolicySnapshot).where(
            ExecutionManagementPolicySnapshot.intent_id == intent.id
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.content_hash == content_hash:
            return ManagementPolicySnapshotFreeze(snapshot=existing, created=False)
        raise ManagementPolicySnapshotRejected(
            "management policy is already frozen for this open trade"
        )

    if ExecutionState(intent.state) is not ExecutionState.PROTECTED:
        raise ManagementPolicySnapshotRejected(
            "management policy can be frozen only in PROTECTED state"
        )

    snapshot = ExecutionManagementPolicySnapshot(
        intent_id=intent.id,
        strategy_version=intent.strategy_version,
        risk_policy_snapshot_id=intent.risk_policy_snapshot_id,
        risk_override_id=intent.risk_override_id,
        risk_policy_json=risk_facts,
        manual_override_json=override_facts,
        exit_profile_json=exit_facts,
        venue_rules_json=venue_facts,
        content_hash=content_hash,
    )
    db.add(snapshot)
    db.flush()
    return ManagementPolicySnapshotFreeze(snapshot=snapshot, created=True)


__all__ = [
    "ManagementPolicySnapshotFreeze",
    "ManagementPolicySnapshotRejected",
    "freeze_management_policy_snapshot",
]
