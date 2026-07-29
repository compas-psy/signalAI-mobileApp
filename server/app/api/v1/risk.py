"""Риск-панель и аварийная остановка (engine-ТЗ §23 блок Risk, §21)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Body, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_config
from ...db import get_db
from ...models import AuditEvent, RiskSnapshot, RiskState
from ...models.enums import ExecutionMode
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


class RiskDashboard(ApiModel):
    taken_at: datetime
    execution_mode: ExecutionMode
    paper_only: bool
    kill_switch: bool
    kill_switch_reason: str
    entries_blocked: bool
    halted: bool
    binding_limit: str
    current_drawdown: Money
    drawdown_multiplier: Money
    limits: list[LimitOut]
    clusters: dict
    # Пока не было ни одного снимка риска, панель обязана сказать это прямо,
    # а не показать нули: ноль расхода читается как «всё свободно».
    has_data: bool
    note: str = ""


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
    # Убытки хранятся со знаком; для лимита важна величина потери.
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


@router.post("/risk/halt", response_model=RiskDashboard)
def halt(
    reason: str = Body(..., embed=True),
    db: Session = Depends(get_db),
) -> RiskDashboard:
    """Аварийная остановка (§21).

    Останавливает **новые** входы. Защитные заявки у брокера не снимаются:
    UX-ТЗ §13.1 — «Kill switch отключает новые execution requests, но не
    удаляет защитные заявки у брокера». Снятая защита превращает аварийную
    остановку в неограниченный риск.
    """
    state = _state(db)
    before = {"kill_switch": state.kill_switch, "reason": state.kill_switch_reason}
    state.kill_switch = True
    state.kill_switch_reason = reason
    db.add(
        AuditEvent(
            actor="owner",
            action="kill_switch_on",
            subject="risk_state",
            detail=reason,
            before_json=before,
            after_json={"kill_switch": True, "reason": reason},
        )
    )
    db.flush()
    return dashboard(db)


@router.post("/risk/resume", response_model=RiskDashboard)
def resume(
    reason: str = Body("", embed=True),
    db: Session = Depends(get_db),
) -> RiskDashboard:
    state = _state(db)
    before = {"kill_switch": state.kill_switch, "reason": state.kill_switch_reason}
    state.kill_switch = False
    state.kill_switch_reason = ""
    db.add(
        AuditEvent(
            actor="owner",
            action="kill_switch_off",
            subject="risk_state",
            detail=reason,
            before_json=before,
            after_json={"kill_switch": False},
        )
    )
    db.flush()
    return dashboard(db)
