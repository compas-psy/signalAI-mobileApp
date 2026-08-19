"""Server-owned execution lifecycle mode API (SAI-030–031 / B6.1–B6.2)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...db import get_db
from ...execution.enums import ExecutionLifecycleMode
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
    """Apply only transitions the server-side SAI-031 guard can prove safe.

    Lower-risk transitions are automatically permitted with an append-only mode
    event. Risk-increasing transitions remain fail-closed until later slices
    supply their real server-side evidence; the request cannot submit or mint
    those proof flags itself.
    """

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


__all__ = [
    "ExecutionModeChangeRequest",
    "ExecutionModeOut",
    "ExecutionModePreviewOut",
    "ExecutionModePreviewRequest",
    "change_execution_mode",
    "get_execution_mode",
    "preview_execution_mode",
    "router",
]
