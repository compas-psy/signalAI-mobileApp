"""Бумажные сделки: чтение и ручное закрытие (engine-ТЗ §18, §21).

Отдельный адрес, а не поле в идее. Идея — предложение, сделка —
исполнение: одна идея может не дать ни одной сделки, а сделка живёт своей
жизнью после того, как идея стала терминальной. Владелец увидел эту
разницу с неприятной стороны — открытая позиция ссылалась на идею,
которой «нет в текущей выдаче».

Отправки заявок здесь нет и не будет: контур бумажный, а живые ордера —
отдельное решение владельца.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import PaperTrade, TradeIdea
from ...models.enums import PaperStatus
from ...schemas.common import ApiModel, Money

router = APIRouter(prefix="/paper", tags=["paper"])


class PaperTradeOut(ApiModel):
    """Сделка так, как её видит владелец."""

    id: str
    idea_id: str
    instrument_id: str
    symbol: str = ""
    direction: str
    status: str

    entry: Money
    initial_stop: Money
    #: Стоп, который защищает позицию сейчас. Отдельно от исходного:
    #: раньше поле было одно, перенос в безубыток жил во временной
    #: переменной пересчёта, и на экране позиция всегда показывала
    #: исходный стоп — даже когда фактически была защищена.
    current_stop: Money
    at_breakeven: bool = False
    breakeven_at: datetime | None = None

    tp_prices: list[str] = Field(default_factory=list)
    tps_taken: int = 0
    tps_total: int = 0
    realized_r: Money = Decimal(0)

    opened_at: datetime
    expires_at: datetime
    last_reconciled_at: datetime | None = None
    #: Сколько часов сделку не с чем было сверить. Молчание источника
    #: обязано быть видно: замершая позиция выглядит живой.
    stale_hours: int | None = None
    closed_at: datetime | None = None
    outcome: str = ""
    close_reason: str = ""


def _out(row: PaperTrade, *, now: datetime) -> PaperTradeOut:
    seen = row.last_reconciled_at
    if seen is not None and seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    stale = None
    if row.status in (PaperStatus.PENDING, PaperStatus.OPEN):
        base = seen or row.opened_at
        if base.tzinfo is None:
            base = base.replace(tzinfo=UTC)
        stale = int((now - base).total_seconds() // 3600)
    return PaperTradeOut(
        id=str(row.id),
        idea_id=str(row.idea_id),
        instrument_id=row.instrument_id,
        symbol=row.instrument_id.split(":")[-1],
        direction=str(row.direction),
        status=str(row.status),
        entry=row.entry,
        initial_stop=row.initial_stop,
        current_stop=row.current_stop,
        at_breakeven=row.breakeven_at is not None,
        breakeven_at=row.breakeven_at,
        tp_prices=[str(p) for p in (row.tp_prices or [])],
        tps_taken=row.tps_taken,
        tps_total=len(row.tp_prices or []),
        realized_r=row.realized_r,
        opened_at=row.opened_at,
        expires_at=row.expires_at,
        last_reconciled_at=row.last_reconciled_at,
        stale_hours=stale,
        closed_at=row.closed_at,
        outcome=row.outcome,
        close_reason=row.close_reason,
    )


@router.get("/trades", response_model=list[PaperTradeOut])
def list_trades(
    live_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[PaperTradeOut]:
    """Сделки бумажного контура, свежие первыми."""
    stmt = select(PaperTrade).order_by(PaperTrade.opened_at.desc()).limit(limit)
    if live_only:
        stmt = stmt.where(
            PaperTrade.status.in_([PaperStatus.PENDING, PaperStatus.OPEN])
        )
    now = datetime.now(UTC)
    return [_out(row, now=now) for row in db.execute(stmt).scalars()]


@router.post("/trades/{trade_id}/close", response_model=PaperTradeOut)
def close_trade(
    trade_id: str, db: Session = Depends(get_db)
) -> PaperTradeOut:
    """Закрыть сделку решением владельца.

    Нужно потому, что автоматика не всесильна: источник может замолчать
    надолго, и владелец вправе снять позицию с сопровождения сам. Причина
    записывается отдельной формулировкой — «закрыто владельцем» и «закрыто
    по стопу» в статистике не должны сливаться.
    """
    row = db.get(PaperTrade, trade_id)
    if row is None:
        raise HTTPException(status_code=404, detail="сделка не найдена")
    if row.status not in (PaperStatus.PENDING, PaperStatus.OPEN):
        raise HTTPException(status_code=409, detail="сделка уже закрыта")

    now = datetime.now(UTC)
    row.status = PaperStatus.CLOSED
    row.closed_at = now
    row.outcome = "ручн."
    row.close_reason = "закрыто владельцем"
    db.flush()
    return _out(row, now=now)


__all__ = ["router"]
