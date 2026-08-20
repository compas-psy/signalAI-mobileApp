"""Server-owned execution lifecycle and owner-control API."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

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
from ...execution.manual_controls import (
    ManualTradeAction,
    ManualTradeControlRejected,
    request_manual_trade_control,
)
from ...execution.mode import (
    ExecutionModeChangeRejected,
    get_execution_mode as read_execution_mode,
)
from ...execution.promotion_guard import (
    change_mode_with_guard,
    preview_promotion,
)
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


class ManualTradeControlRequest(ApiModel):
    """Owner intent only; execution economics remain server-revalidated."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        extra="forbid",
    )

    action: ManualTradeAction
    quantity: Money | None = None
    stop_price: Money | None = None
    reason: str


class ManualTradeControlOut(ApiModel):
    command_id: uuid.UUID
    intent_id: uuid.UUID
    management_policy_snapshot_id: uuid.UUID
    action: ManualTradeAction
    status: str
    reduce_only: bool
    quantity: Money | None
    stop_price: Money | None
    order_id: uuid.UUID | None
    order_status: str | None
    created: bool


def _idempotency_key(
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_idempotency_key: str | None = Header(None, alias="X-Idempotency-Key"),
) -> str:
    """Resolve the standard/mobile replay key without choosing on conflict."""

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


# Backward-compatible internal dependency name used by the LIVE activation
# endpoint/tests. Both write boundaries intentionally share the same header
# semantics and conflict behavior.
_live_idempotency_key = _idempotency_key


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
    "/execution/intents/{intent_id}/control",
    response_model=ManualTradeControlOut,
)
def request_execution_manual_control(
    intent_id: uuid.UUID,
    request: ManualTradeControlRequest,
    idempotency_key: str = Depends(_idempotency_key),
    db: Session = Depends(get_db),
) -> ManualTradeControlOut:
    """Persist one monotonic owner action; no provider I/O occurs here."""

    try:
        result = request_manual_trade_control(
            db,
            intent_id=intent_id,
            action=request.action,
            idempotency_key=idempotency_key,
            owner_reason=request.reason,
            requested_quantity=request.quantity,
            requested_stop=request.stop_price,
        )
    except ManualTradeControlRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    command = result.command
    order = result.order
    return ManualTradeControlOut(
        command_id=command.id,
        intent_id=command.intent_id,
        management_policy_snapshot_id=command.management_policy_snapshot_id,
        action=ManualTradeAction(command.action),
        status=command.status,
        reduce_only=bool(command.reduce_only),
        quantity=(order.quantity if order is not None else command.requested_quantity),
        stop_price=(order.stop_price if order is not None else command.requested_stop),
        order_id=(order.id if order is not None else None),
        order_status=(order.status if order is not None else None),
        created=result.created,
    )


__all__ = [
    "ExecutionModeChangeRequest",
    "ExecutionModeOut",
    "ExecutionModePreviewOut",
    "ExecutionModePreviewRequest",
    "LiveActivationConfirmRequest",
    "LiveActivationPreviewOut",
    "LiveActivationResultOut",
    "ManualTradeControlOut",
    "ManualTradeControlRequest",
    "change_execution_mode",
    "confirm_live_activation",
    "get_execution_mode",
    "preview_execution_mode",
    "preview_live_activation",
    "request_execution_manual_control",
    "router",
]