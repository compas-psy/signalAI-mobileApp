"""Server-owned execution lifecycle and bounded owner-control API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import ConfigDict
from sqlalchemy.orm import Session

from ...db import get_db
from ...execution.enums import ExecutionLifecycleMode
from ...execution.live_activation import (
    LiveActivationRejected,
    confirm_live_activation as apply_live_activation_confirmation,
    create_live_activation_preview,
)
from ...execution.mode import (
    ExecutionModeChangeRejected,
    get_execution_mode as read_execution_mode,
)
from ...execution.promotion_guard import (
    change_mode_with_guard,
    preview_promotion,
)
from ...execution.risk_on import (
    RiskOnConfirmationRejected,
    RiskOnPreviewRejected,
    confirm_risk_on as apply_risk_on_confirmation,
    preview_risk_on as build_risk_on_preview,
)
from ...execution.risk_override import ExecutionRiskOverrideRejected
from ...schemas.common import ApiModel, Money

router = APIRouter(tags=["execution"])


class ExecutionModeOut(ApiModel):
    mode: ExecutionLifecycleMode
    updated_at: datetime


class ExecutionModePreviewRequest(ApiModel):
    target: ExecutionLifecycleMode


class ExecutionModePreviewOut(ApiModel):
    current: ExecutionLifecycleMode
    target: ExecutionLifecycleMode
    allowed: bool
    blockers: list[str]


class ExecutionModeChangeRequest(ApiModel):
    target: ExecutionLifecycleMode
    reason: str


class LiveActivationPreviewOut(ApiModel):
    preview_hash: str
    from_mode: ExecutionLifecycleMode
    target_mode: ExecutionLifecycleMode
    venue: str
    account: str
    capital_rub: Decimal
    hard_caps: dict[str, str]
    config_hash: str
    allowed: bool
    blockers: list[str]


class LiveActivationConfirmRequest(ApiModel):
    preview_hash: str
    owner_confirmed: bool


class LiveActivationResultOut(ApiModel):
    preview_hash: str
    idempotency_key: str
    status: str
    mode: ExecutionLifecycleMode
    blockers: list[str]


class _StrictApiModel(ApiModel):
    """Owner write payloads fail closed on fields the phone must not own."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        extra="forbid",
    )


class RiskOnPreviewRequest(_StrictApiModel):
    idea_id: UUID
    venue: str
    account: str


class RiskOnPreviewOut(ApiModel):
    idea_id: UUID
    risk_snapshot_id: UUID
    venue: str
    account: str
    allowed: bool
    blockers: list[str]
    base_risk_pct: Money
    effective_risk_pct: Money
    hard_cap_risk_pct: Money
    base_quantity: Money
    effective_quantity: Money
    effective_risk_amount: Money
    effective_leverage: Money | None
    hard_cap_leverage: Money
    binding_limit: str
    preview_hash: str


class RiskOnConfirmRequest(_StrictApiModel):
    idea_id: UUID
    venue: str
    account: str
    preview_hash: str
    owner_confirmed: bool


class RiskOnConfirmOut(ApiModel):
    risk_override_id: UUID
    created: bool
    preview_hash: str
    venue: str
    account: str
    effective_risk_pct: Money
    effective_quantity: Money
    effective_leverage: Money | None
    hard_cap_risk_pct: Money
    hard_cap_leverage: Money | None


def _live_idempotency_key(
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_idempotency_key: str | None = Header(None, alias="X-Idempotency-Key"),
) -> str:
    """Accept the historical mobile header without breaking standard callers.

    ApiClient has long used ``X-Idempotency-Key`` across owner write endpoints,
    while SAI-032 initially exposed only ``Idempotency-Key``. Supporting both at
    this boundary keeps replay safety intact and avoids changing unrelated API
    clients. Conflicting dual headers fail closed instead of choosing one.
    """

    standard = (idempotency_key or "").strip()
    mobile = (x_idempotency_key or "").strip()
    if standard and mobile and standard != mobile:
        raise HTTPException(
            status_code=409,
            detail="conflicting Idempotency-Key and X-Idempotency-Key headers",
        )
    resolved = mobile or standard
    if not resolved:
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key or X-Idempotency-Key is required",
        )
    return resolved


@router.get("/execution/mode", response_model=ExecutionModeOut)
def get_execution_mode(db: Session = Depends(get_db)) -> ExecutionModeOut:
    snapshot = read_execution_mode(db)
    return ExecutionModeOut.model_validate(snapshot)


@router.post("/execution/mode/preview", response_model=ExecutionModePreviewOut)
def preview_execution_mode(
    request: ExecutionModePreviewRequest,
    db: Session = Depends(get_db),
) -> ExecutionModePreviewOut:
    preview = preview_promotion(db, target=request.target)
    return ExecutionModePreviewOut(
        current=preview.current,
        target=preview.target,
        allowed=preview.allowed,
        blockers=list(preview.blockers),
    )


@router.post("/execution/mode/change", response_model=ExecutionModeOut)
def change_execution_mode(
    request: ExecutionModeChangeRequest,
    db: Session = Depends(get_db),
) -> ExecutionModeOut:
    """Apply generic safe transitions; LIVE cannot bypass the two-step flow."""

    try:
        snapshot = change_mode_with_guard(
            db,
            target=request.target,
            actor="owner",
            reason=request.reason,
        )
    except ExecutionModeChangeRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ExecutionModeOut.model_validate(snapshot)


@router.post(
    "/execution/live/preview",
    response_model=LiveActivationPreviewOut,
)
def preview_live_activation(
    db: Session = Depends(get_db),
) -> LiveActivationPreviewOut:
    """Step 1: persist and return the exact LIVE context shown to owner."""

    preview = create_live_activation_preview(db)
    return LiveActivationPreviewOut(
        preview_hash=preview.preview_hash,
        from_mode=preview.from_mode,
        target_mode=preview.target_mode,
        venue=preview.venue,
        account=preview.account,
        capital_rub=preview.capital_rub,
        hard_caps=preview.hard_caps,
        config_hash=preview.config_hash,
        allowed=preview.allowed,
        blockers=list(preview.blockers),
    )


@router.post(
    "/execution/live/confirm",
    response_model=LiveActivationResultOut,
)
def confirm_live_activation(
    request: LiveActivationConfirmRequest,
    idempotency_key: str = Depends(_live_idempotency_key),
    db: Session = Depends(get_db),
) -> LiveActivationResultOut:
    """Step 2: explicit owner confirmation followed by fresh server recheck."""

    try:
        result = apply_live_activation_confirmation(
            db,
            preview_hash=request.preview_hash,
            idempotency_key=idempotency_key,
            owner_confirmed=request.owner_confirmed,
        )
    except LiveActivationRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return LiveActivationResultOut(
        preview_hash=result.preview_hash,
        idempotency_key=result.idempotency_key,
        status=result.status,
        mode=result.mode,
        blockers=list(result.blockers),
    )


@router.post(
    "/execution/risk-on/preview",
    response_model=RiskOnPreviewOut,
)
def preview_risk_on(
    request: RiskOnPreviewRequest,
    db: Session = Depends(get_db),
) -> RiskOnPreviewOut:
    """Return server-calculated RISK_ON economics for one execution scope.

    The payload intentionally has no risk, quantity or leverage fields. Extra
    fields are rejected by Pydantic so a compromised/stale mobile client cannot
    become an economic source of truth accidentally.
    """

    try:
        preview = build_risk_on_preview(
            db,
            idea_id=request.idea_id,
            venue=request.venue,
            account=request.account,
        )
    except RiskOnPreviewRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RiskOnPreviewOut(
        idea_id=preview.idea_id,
        risk_snapshot_id=preview.risk_snapshot_id,
        venue=preview.venue,
        account=preview.account,
        allowed=preview.allowed,
        blockers=list(preview.blockers),
        base_risk_pct=preview.base_risk_pct,
        effective_risk_pct=preview.effective_risk_pct,
        hard_cap_risk_pct=preview.hard_cap_risk_pct,
        base_quantity=preview.base_quantity,
        effective_quantity=preview.effective_quantity,
        effective_risk_amount=preview.effective_risk_amount,
        effective_leverage=preview.effective_leverage,
        hard_cap_leverage=preview.hard_cap_leverage,
        binding_limit=preview.binding_limit,
        preview_hash=preview.preview_hash,
    )


@router.post(
    "/execution/risk-on/confirm",
    response_model=RiskOnConfirmOut,
)
def confirm_risk_on(
    request: RiskOnConfirmRequest,
    idempotency_key: str = Depends(_live_idempotency_key),
    db: Session = Depends(get_db),
) -> RiskOnConfirmOut:
    """Recalculate the shown preview and persist one immutable owner override."""

    try:
        result = apply_risk_on_confirmation(
            db,
            idea_id=request.idea_id,
            venue=request.venue,
            account=request.account,
            preview_hash=request.preview_hash,
            idempotency_key=idempotency_key,
            owner_confirmed=request.owner_confirmed,
        )
    except (RiskOnConfirmationRejected, ExecutionRiskOverrideRejected) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    override = result.override
    return RiskOnConfirmOut(
        risk_override_id=override.id,
        created=result.created,
        preview_hash=override.preview_hash,
        venue=override.venue,
        account=override.account,
        effective_risk_pct=override.effective_risk_pct,
        effective_quantity=override.effective_quantity,
        effective_leverage=override.effective_leverage,
        hard_cap_risk_pct=override.hard_cap_risk_pct,
        hard_cap_leverage=override.hard_cap_leverage,
    )


__all__ = [
    "ExecutionModeChangeRequest",
    "ExecutionModeOut",
    "ExecutionModePreviewOut",
    "ExecutionModePreviewRequest",
    "LiveActivationConfirmRequest",
    "LiveActivationPreviewOut",
    "LiveActivationResultOut",
    "RiskOnConfirmOut",
    "RiskOnConfirmRequest",
    "RiskOnPreviewOut",
    "RiskOnPreviewRequest",
    "change_execution_mode",
    "confirm_live_activation",
    "confirm_risk_on",
    "get_execution_mode",
    "preview_execution_mode",
    "preview_live_activation",
    "preview_risk_on",
    "router",
]
