"""Явные действия владельца над серверным контуром.

Планировщик остаётся основным механизмом. Эти endpoints нужны не для второго
расписания, а для кнопки «проверить сейчас» после настройки источника или
сомнительной идеи. Каждый вызов выполняет один последовательный проход и
возвращает фактический результат; скрытого фонового запуска нет.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...db import get_db
from ...paper.tracker import track
from ...pipeline.supervise import supervise
from ...research.collector import collect_all
from ...research.pipeline import expire as expire_hypotheses
from ...research.run_engines import run_demand
from ...research.sources import readiness, sync_registry
from ...schemas.common import ApiModel

router = APIRouter(prefix="/control", tags=["control"])


class ActionOut(ApiModel):
    ok: bool = True
    action: str
    detail: str
    data: dict = {}


@router.post("/ideas/reconcile", response_model=ActionOut)
def reconcile_ideas(db: Session = Depends(get_db)) -> ActionOut:
    watched = supervise(db)
    paper = track(db)
    db.flush()
    return ActionOut(
        action="ideas_reconcile",
        detail=(
            f"идей проверено {watched.checked}, закрыто как отработавшие/сломанные "
            f"{watched.changed}; {paper.summary()}"
        ),
        data={
            "ideas_checked": watched.checked,
            "ideas_changed": watched.changed,
            "missed": watched.missed,
            "cancelled": watched.cancelled,
            "timed_out": watched.timed_out,
        },
    )


@router.post("/research/refresh", response_model=ActionOut)
def refresh_research(db: Session = Depends(get_db)) -> ActionOut:
    sync_registry(db)
    collected = collect_all(db)
    db.flush()
    engines = run_demand(db)
    expired = expire_hypotheses(db)
    state = readiness(db)
    db.flush()
    return ActionOut(
        action="research_refresh",
        detail=(
            f"сбор: {collected.summary()}; движки: {engines.summary()}; "
            f"источников с бесплатным маршрутом {state['free_route']}/{state['total']}"
        ),
        data={
            "collection": collected.summary(),
            "engines": engines.summary(),
            "expired_hypotheses": expired,
            "sources_total": state["total"],
            "sources_free": state["free_route"],
        },
    )


__all__ = ["router"]
