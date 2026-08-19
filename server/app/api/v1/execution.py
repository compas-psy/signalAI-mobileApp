"""Server-owned execution lifecycle mode API (SAI-030 / B6.1)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...db import get_db
from ...execution.enums import ExecutionLifecycleMode
from ...execution.mode import (
    ExecutionModeChangeRejected,
    change_execution_mode as apply_execution_mode_change,
    get_execution_mode as read_execution_mode,
    preview_execution_mode as preview_mode_change,
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
    preview = preview_mode_change(db, target=request.target)
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
    """Apply an idempotent same-mode request; other changes await SAI-031.

    No request field can manufacture promotion authorization. SAI-031 will
    recheck server-side gates and call the internal service with its own proof.
    """

    try:
        snapshot = apply_execution_mode_change(
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
