"""Server-owned execution lifecycle mode API (SAI-030–032 / B6.1–B6.3)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException
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
from ...schemas.common import ApiModel

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
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
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


__all__ = [
    "ExecutionModeChangeRequest",
    "ExecutionModeOut",
    "ExecutionModePreviewOut",
    "ExecutionModePreviewRequest",
    "LiveActivationConfirmRequest",
    "LiveActivationPreviewOut",
    "LiveActivationResultOut",
    "change_execution_mode",
    "confirm_live_activation",
    "get_execution_mode",
    "preview_execution_mode",
    "preview_live_activation",
    "router",
]
