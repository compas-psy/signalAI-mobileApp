"""Риск-панель, ручной risk preview и аварийная остановка."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_config
from ...db import get_db
from ...execution.enums import ExecutionKillSwitchLevel, ExecutionLifecycleMode
from ...execution.kill_switch import (
    ExecutionKillSwitchError,
    clear_execution_kill_switch,
    effective_execution_kill_switch_level,
    set_execution_kill_switch,
)
from ...models import RiskSnapshot, RiskState
from ...models.enums import ExecutionMode
from ...risk.manual_preview import ManualRiskPreviewRejected, preview_manual_risk
from ...schemas.common import ApiModel, Money

router = APIRouter(tags=["risk"])


class LimitOut(ApiModel):
    """Один лимит: сколько можно, сколько израсходовано, что осталось."""

    name: str
    label: str
    limit: Money
    used: Money
    remaining: Money
    breached: bool


class KillSwitchRequest(ApiModel):
    """One explicit SAI-028 kill-switch action."""

    level: ExecutionKillSwitchLevel
    reason: str
    confirm_flatten_all: bool = False


class RiskDashboard(ApiModel):
    taken_at: datetime
    execution_mode: ExecutionMode
    paper_only: bool
    kill_switch: bool
    kill_switch_level: ExecutionKillSwitchLevel
    kill_switch_reason: str
    entries_blocked: bool
    halted: bool
    binding_limit: str
    current_drawdown: Money
    drawdown_multiplier: Money
    limits: list[LimitOut]
    clusters: dict
    has_data: bool
    note: str = ""


class _StrictApiModel(ApiModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        extra="forbid",
    )


class ManualRiskPreviewRequest(_StrictApiModel):
    """B7.3 input: identity + named preset + client-observed server mode only."""

    idea_id: UUID
    preset_id: str
    current_mode: ExecutionLifecycleMode


class ManualRiskPreviewOut(ApiModel):
    idea_id: UUID
    risk_snapshot_id: UUID
    preset_id: str
    execution_mode: ExecutionLifecycleMode
    allowed: bool
    warnings: list[str]
    blockers: list[str]
    auto_risk_pct: Money
    auto_risk_amount: Money
    requested_risk_pct: Money
    requested_risk_amount: Money
    effective_risk_pct: Money
    effective_risk_amount: Money
    hard_cap_risk_pct: Money
    quantity: Money
    notional: Money
    resulting_leverage: Money | None
    liquidation_distance_ratio: Money | None
    total_open_risk_after: Money
    cluster_risk_after: Money
    worst_case_stop_loss: Money
    binding_constraint: str
    issued_at: datetime
    expires_at: datetime
    preview_hash: str


def _state(db: Session) -> RiskState:
    state = db.get(RiskState, 1)
    if state is None:
        state = RiskState(id=1, execution_mode=ExecutionMode.PAPER)
        db.add(state)
        db.flush()
    return state


def _limits(cfg, snap: RiskSnapshot | None) -> list[LimitOut]:
    def row(name: str, label: str, limit_path: str, used: Decimal) -> LimitOut:
        limit = cfg.decimal(limit_path)
        return LimitOut(
            name=name,
            label=label,
            limit=limit,
            used=used,
            remaining=max(Decimal(0), limit - used),
            breached=used >= limit,
        )

    zero = Decimal(0)
    day = -min(zero, snap.day_pnl_pct) if snap else zero
    week = -min(zero, snap.week_pnl_pct) if snap else zero
    month = -min(zero, snap.month_pnl_pct) if snap else zero
    return [
        row("daily", "Дневной убыток", "risk.daily_loss_limit", day),
        row("weekly", "Недельный убыток", "risk.weekly_loss_limit", week),
        row("monthly", "Месячный убыток", "risk.monthly_loss_limit", month),
        row(
            "open",
            "Открытый риск",
            "risk.max_total_open_risk",
            snap.open_risk if snap else zero,
        ),
        row("cluster", "Риск кластера", "risk.max_cluster_risk", zero),
    ]


@router.get("/risk/dashboard", response_model=RiskDashboard)
def dashboard(db: Session = Depends(get_db)) -> RiskDashboard:
    cfg = get_config()
    state = _state(db)
    snap = db.execute(
        select(RiskSnapshot).order_by(RiskSnapshot.taken_at.desc()).limit(1)
    ).scalar_one_or_none()

    return RiskDashboard(
        taken_at=snap.taken_at if snap else datetime.now(UTC),
        execution_mode=state.execution_mode,
        paper_only=bool(cfg.get("risk.paper_only")),
        kill_switch=state.kill_switch,
        kill_switch_level=effective_execution_kill_switch_level(state),
        kill_switch_reason=state.kill_switch_reason,
        entries_blocked=snap.entries_blocked if snap else False,
        halted=snap.halted if snap else False,
        binding_limit=snap.binding_limit if snap else "none",
        current_drawdown=snap.current_drawdown if snap else Decimal(0),
        drawdown_multiplier=snap.drawdown_multiplier if snap else Decimal(1),
        limits=_limits(cfg, snap),
        clusters=snap.cluster_risk_json if snap else {},
        has_data=snap is not None,
        note=(
            ""
            if snap
            else "снимков риска ещё нет: расход лимитов появится после первой "
            "сделки. Нули означают отсутствие данных, а не свободный лимит."
        ),
    )


@router.post("/risk/preview", response_model=ManualRiskPreviewOut)
def manual_risk_preview(
    request: ManualRiskPreviewRequest,
    db: Session = Depends(get_db),
) -> ManualRiskPreviewOut:
    """SAI-043: signed short-lived, server-owned risk preview.

    The client cannot submit multiplier, risk, quantity, leverage or any other
    money-bearing value. Unknown fields fail validation before domain logic.
    """

    try:
        preview = preview_manual_risk(
            db,
            idea_id=request.idea_id,
            preset_id=request.preset_id,
            current_mode=request.current_mode,
        )
    except ManualRiskPreviewRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ManualRiskPreviewOut(
        idea_id=preview.idea_id,
        risk_snapshot_id=preview.risk_snapshot_id,
        preset_id=preview.preset_id,
        execution_mode=preview.execution_mode,
        allowed=preview.allowed,
        warnings=list(preview.warnings),
        blockers=list(preview.blockers),
        auto_risk_pct=preview.auto_risk_pct,
        auto_risk_amount=preview.auto_risk_amount,
        requested_risk_pct=preview.requested_risk_pct,
        requested_risk_amount=preview.requested_risk_amount,
        effective_risk_pct=preview.effective_risk_pct,
        effective_risk_amount=preview.effective_risk_amount,
        hard_cap_risk_pct=preview.hard_cap_risk_pct,
        quantity=preview.quantity,
        notional=preview.notional,
        resulting_leverage=preview.resulting_leverage,
        liquidation_distance_ratio=preview.liquidation_distance_ratio,
        total_open_risk_after=preview.total_open_risk_after,
        cluster_risk_after=preview.cluster_risk_after,
        worst_case_stop_loss=preview.worst_case_stop_loss,
        binding_constraint=preview.binding_constraint,
        issued_at=preview.issued_at,
        expires_at=preview.expires_at,
        preview_hash=preview.preview_hash,
    )


@router.post("/risk/kill-switch", response_model=RiskDashboard)
def set_kill_switch(
    request: KillSwitchRequest,
    db: Session = Depends(get_db),
) -> RiskDashboard:
    try:
        set_execution_kill_switch(
            db,
            level=request.level,
            actor="owner",
            reason=request.reason,
            confirm_flatten_all=request.confirm_flatten_all,
        )
    except ExecutionKillSwitchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return dashboard(db)


@router.post("/risk/halt", response_model=RiskDashboard)
def halt(
    reason: str = Body(..., embed=True),
    db: Session = Depends(get_db),
) -> RiskDashboard:
    try:
        set_execution_kill_switch(
            db,
            level=ExecutionKillSwitchLevel.HALT_NEW_ENTRIES,
            actor="owner",
            reason=reason,
            audit_action="kill_switch_on",
        )
    except ExecutionKillSwitchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return dashboard(db)


@router.post("/risk/resume", response_model=RiskDashboard)
def resume(
    reason: str = Body("", embed=True),
    db: Session = Depends(get_db),
) -> RiskDashboard:
    clear_execution_kill_switch(
        db,
        actor="owner",
        reason=reason,
        audit_action="kill_switch_off",
    )
    return dashboard(db)
