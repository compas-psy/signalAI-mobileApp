"""Durable bounded owner risk overrides (SAI-042).

This service stores a single immutable owner decision produced from an internal
risk-policy authorization. It does not calculate the preview itself, expose a
public API, or move money. SAI-043 owns preview/provider/UI wiring.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..models.execution import ExecutionRiskOverride
from ..models.ideas import TradeIdea
from ..models.risk import AuditEvent, RiskSnapshot
from .enums import ExecutionLifecycleMode
from .mode import get_execution_mode


class ExecutionRiskOverrideRejected(ValueError):
    """A risk-increasing owner decision failed an authoritative safety gate."""


@dataclass(frozen=True)
class RiskOverrideAuthorization:
    """Internal proof minted by the authoritative risk-preview policy."""

    allowed: bool
    actor: str
    reason: str
    hard_cap_risk_pct: Decimal
    hard_cap_leverage: Decimal | None = None
    detail_json: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskOverrideRequest:
    idea_id: uuid.UUID
    risk_snapshot_id: uuid.UUID
    preset: str
    venue: str
    account: str
    effective_risk_pct: Decimal
    effective_quantity: Decimal
    effective_leverage: Decimal | None
    idempotency_key: str
    owner_confirmed: bool
    reason: str


@dataclass(frozen=True)
class RiskOverrideCreation:
    override: ExecutionRiskOverride
    created: bool


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(Decimal(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _preview_hash(
    *,
    request: RiskOverrideRequest,
    idea: TradeIdea,
    mode: ExecutionLifecycleMode,
    authorization: RiskOverrideAuthorization,
) -> str:
    payload = {
        "account": request.account,
        "authorization_actor": authorization.actor.strip(),
        "authorization_detail": authorization.detail_json,
        "authorization_reason": authorization.reason.strip(),
        "base_quantity": _decimal_text(Decimal(idea.quantity)),
        "base_risk_pct": _decimal_text(Decimal(idea.risk_pct)),
        "effective_leverage": _decimal_text(request.effective_leverage),
        "effective_quantity": _decimal_text(request.effective_quantity),
        "effective_risk_pct": _decimal_text(request.effective_risk_pct),
        "execution_mode": mode.value,
        "hard_cap_leverage": _decimal_text(authorization.hard_cap_leverage),
        "hard_cap_risk_pct": _decimal_text(authorization.hard_cap_risk_pct),
        "idea_id": str(request.idea_id),
        "preset": request.preset,
        "reason": request.reason.strip(),
        "risk_snapshot_id": str(request.risk_snapshot_id),
        "venue": request.venue,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate(
    db: Session,
    *,
    request: RiskOverrideRequest,
    authorization: RiskOverrideAuthorization,
) -> tuple[TradeIdea, RiskSnapshot, ExecutionLifecycleMode]:
    if not request.owner_confirmed:
        raise ExecutionRiskOverrideRejected("explicit owner confirmation is required")
    if request.preset != "RISK_ON":
        raise ExecutionRiskOverrideRejected("unsupported risk override preset")
    if not request.venue.strip() or not request.account.strip():
        raise ExecutionRiskOverrideRejected("venue and account are required")
    if not request.idempotency_key.strip():
        raise ExecutionRiskOverrideRejected("idempotency key is required")
    if not request.reason.strip():
        raise ExecutionRiskOverrideRejected("owner reason is required")

    if not authorization.allowed:
        raise ExecutionRiskOverrideRejected("risk-policy authorization denied override")
    if not authorization.actor.strip() or not authorization.reason.strip():
        raise ExecutionRiskOverrideRejected(
            "risk-policy authorization actor and reason are required"
        )

    hard_risk = Decimal(authorization.hard_cap_risk_pct)
    if hard_risk <= 0:
        raise ExecutionRiskOverrideRejected("hard risk cap must be positive")

    idea = db.get(TradeIdea, request.idea_id)
    if idea is None:
        raise ExecutionRiskOverrideRejected("idea_id does not exist")
    risk = db.get(RiskSnapshot, request.risk_snapshot_id)
    if risk is None:
        raise ExecutionRiskOverrideRejected("risk_snapshot_id does not exist")
    if risk.entries_blocked or risk.halted:
        raise ExecutionRiskOverrideRejected("risk snapshot blocks new entries")

    base_risk = Decimal(idea.risk_pct)
    base_quantity = Decimal(idea.quantity)
    effective_risk = Decimal(request.effective_risk_pct)
    effective_quantity = Decimal(request.effective_quantity)
    if effective_risk <= base_risk:
        raise ExecutionRiskOverrideRejected(
            "RISK_ON effective risk must be greater than the base risk"
        )
    if effective_risk > hard_risk:
        raise ExecutionRiskOverrideRejected("effective risk exceeds hard risk cap")
    if effective_quantity <= 0 or effective_quantity < base_quantity:
        raise ExecutionRiskOverrideRejected(
            "RISK_ON effective quantity must be positive and not below base quantity"
        )

    if request.effective_leverage is not None:
        effective_leverage = Decimal(request.effective_leverage)
        hard_leverage = (
            Decimal(authorization.hard_cap_leverage)
            if authorization.hard_cap_leverage is not None
            else None
        )
        if effective_leverage <= 0:
            raise ExecutionRiskOverrideRejected("effective leverage must be positive")
        if hard_leverage is None or hard_leverage <= 0:
            raise ExecutionRiskOverrideRejected(
                "hard leverage cap is required for a leveraged override"
            )
        if effective_leverage > hard_leverage:
            raise ExecutionRiskOverrideRejected(
                "effective leverage exceeds hard leverage cap"
            )

    return idea, risk, get_execution_mode(db).mode


def _same_material_override(
    override: ExecutionRiskOverride,
    *,
    preview_hash: str,
) -> bool:
    return override.preview_hash == preview_hash


def create_execution_risk_override(
    db: Session,
    *,
    request: RiskOverrideRequest,
    authorization: RiskOverrideAuthorization,
) -> RiskOverrideCreation:
    """Persist one bounded owner override, retry-safe under concurrency."""

    idea, risk, mode = _validate(
        db,
        request=request,
        authorization=authorization,
    )
    preview_hash = _preview_hash(
        request=request,
        idea=idea,
        mode=mode,
        authorization=authorization,
    )

    existing_by_key = db.execute(
        select(ExecutionRiskOverride).where(
            ExecutionRiskOverride.idempotency_key == request.idempotency_key
        )
    ).scalar_one_or_none()
    if existing_by_key is not None:
        if not _same_material_override(existing_by_key, preview_hash=preview_hash):
            raise ExecutionRiskOverrideRejected(
                "idempotency key is already bound to a different risk override"
            )
        return RiskOverrideCreation(override=existing_by_key, created=False)

    detail = dict(authorization.detail_json)
    detail.update(
        {
            "authorization_actor": authorization.actor.strip(),
            "authorization_reason": authorization.reason.strip(),
        }
    )
    values = {
        "idea_id": idea.id,
        "risk_snapshot_id": risk.id,
        "preset": request.preset,
        "venue": request.venue,
        "account": request.account,
        "execution_mode_snapshot": mode.value,
        "base_risk_pct": Decimal(idea.risk_pct),
        "effective_risk_pct": Decimal(request.effective_risk_pct),
        "hard_cap_risk_pct": Decimal(authorization.hard_cap_risk_pct),
        "base_quantity": Decimal(idea.quantity),
        "effective_quantity": Decimal(request.effective_quantity),
        "effective_leverage": (
            Decimal(request.effective_leverage)
            if request.effective_leverage is not None
            else None
        ),
        "hard_cap_leverage": (
            Decimal(authorization.hard_cap_leverage)
            if authorization.hard_cap_leverage is not None
            else None
        ),
        "preview_hash": preview_hash,
        "idempotency_key": request.idempotency_key,
        "actor": "owner",
        "reason": request.reason.strip(),
        "detail_json": detail,
    }
    created_id = db.execute(
        insert(ExecutionRiskOverride)
        .values(**values)
        .on_conflict_do_nothing()
        .returning(ExecutionRiskOverride.id)
    ).scalar_one_or_none()

    if created_id is not None:
        override = db.get(ExecutionRiskOverride, created_id)
        if override is None:
            raise RuntimeError("created risk override could not be reloaded")
        db.add(
            AuditEvent(
                actor="owner",
                action="execution_risk_override_created",
                subject=str(idea.id),
                detail=request.reason.strip(),
                before_json={
                    "risk_pct": _decimal_text(Decimal(idea.risk_pct)),
                    "quantity": _decimal_text(Decimal(idea.quantity)),
                },
                after_json={
                    "risk_pct": _decimal_text(Decimal(request.effective_risk_pct)),
                    "quantity": _decimal_text(Decimal(request.effective_quantity)),
                    "effective_leverage": _decimal_text(request.effective_leverage),
                    "hard_cap_risk_pct": _decimal_text(
                        Decimal(authorization.hard_cap_risk_pct)
                    ),
                    "hard_cap_leverage": _decimal_text(
                        authorization.hard_cap_leverage
                    ),
                    "execution_mode": mode.value,
                    "venue": request.venue,
                    "account": request.account,
                    "preview_hash": preview_hash,
                },
            )
        )
        db.flush()
        return RiskOverrideCreation(override=override, created=True)

    existing = db.execute(
        select(ExecutionRiskOverride).where(
            or_(
                ExecutionRiskOverride.idempotency_key == request.idempotency_key,
                ExecutionRiskOverride.preview_hash == preview_hash,
            )
        )
    ).scalars().first()
    if existing is None:
        raise RuntimeError("risk override conflict did not resolve to a durable row")
    if existing.idempotency_key == request.idempotency_key and not _same_material_override(
        existing,
        preview_hash=preview_hash,
    ):
        raise ExecutionRiskOverrideRejected(
            "idempotency key is already bound to a different risk override"
        )
    if not _same_material_override(existing, preview_hash=preview_hash):
        raise ExecutionRiskOverrideRejected("risk override conflicts with existing fact")
    return RiskOverrideCreation(override=existing, created=False)


__all__ = [
    "ExecutionRiskOverrideRejected",
    "RiskOverrideAuthorization",
    "RiskOverrideCreation",
    "RiskOverrideRequest",
    "create_execution_risk_override",
]
