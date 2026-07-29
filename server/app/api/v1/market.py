"""Рыночные данные (engine-ТЗ §23, блок Market)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Bar, Instrument, RegimeSnapshot
from ...models.enums import Timeframe, Venue
from ...schemas.common import ApiModel, Money

router = APIRouter(tags=["market"])


class InstrumentOut(ApiModel):
    instrument_id: str
    venue: Venue
    asset_class: str
    symbol: str
    title: str
    currency: str
    tick_size: Money
    tick_value: Money
    lot_size: int
    quantity_step: Money
    min_quantity: Money
    contract_multiplier: Money
    expiry: datetime | None = None
    correlation_cluster: str | None = None
    in_universe: bool
    universe_note: str


class BarOut(ApiModel):
    open_time: datetime
    open: Money
    high: Money
    low: Money
    close: Money
    volume_units: Money | None = None
    volume_notional: Money | None = None
    open_interest: Money | None = None
    # §4.4 запрещает использовать незакрытую свечу как закрытую, поэтому
    # признак едет вместе с данными, а не подразумевается.
    is_closed: bool
    source: str
    quality_flags: list[str]


class RegimeOut(ApiModel):
    bar_time: datetime
    timeframe: Timeframe
    trend: str
    trend_score: int
    volatility: str
    liquidity: str
    derivatives_flow: str
    detail: dict


@router.get("/instruments", response_model=list[InstrumentOut])
def list_instruments(
    venue: Venue | None = None,
    in_universe: bool | None = None,
    db: Session = Depends(get_db),
) -> list[Instrument]:
    stmt = select(Instrument).order_by(Instrument.instrument_id)
    if venue is not None:
        stmt = stmt.where(Instrument.venue == venue)
    if in_universe is not None:
        stmt = stmt.where(Instrument.in_universe.is_(in_universe))
    return list(db.execute(stmt).scalars())


def _require_instrument(db: Session, instrument_id: str) -> Instrument:
    # Ищем по канонической строке, а не по первичному ключу: наружу инструмент
    # известен как «MOEX:FUT:SIU6», внутренний UUID клиента не касается.
    item = db.execute(
        select(Instrument).where(Instrument.instrument_id == instrument_id)
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, f"инструмент {instrument_id} не найден")
    return item


@router.get("/market/{instrument_id:path}/bars", response_model=list[BarOut])
def get_bars(
    instrument_id: str,
    timeframe: Timeframe = Query(...),
    limit: int = Query(300, ge=1, le=5000),
    closed_only: bool = Query(True),
    db: Session = Depends(get_db),
) -> list[Bar]:
    """Свечи в обратном хронологическом порядке, по умолчанию только закрытые.

    ``closed_only`` по умолчанию истинно намеренно: расчёт, случайно
    захвативший формирующийся бар, даёт результат, который меняется сам по
    себе между двумя запросами.
    """
    _require_instrument(db, instrument_id)
    stmt = (
        select(Bar)
        .where(Bar.instrument_id == instrument_id, Bar.timeframe == timeframe)
        .order_by(Bar.open_time.desc())
        .limit(limit)
    )
    if closed_only:
        stmt = stmt.where(Bar.is_closed.is_(True))
    return list(reversed(list(db.execute(stmt).scalars())))


@router.get("/market/{instrument_id:path}/regime", response_model=RegimeOut)
def get_regime(
    instrument_id: str,
    timeframe: Timeframe = Query(Timeframe.D1),
    db: Session = Depends(get_db),
) -> RegimeOut:
    _require_instrument(db, instrument_id)
    row = db.execute(
        select(RegimeSnapshot)
        .where(
            RegimeSnapshot.instrument_id == instrument_id,
            RegimeSnapshot.timeframe == timeframe,
        )
        .order_by(RegimeSnapshot.bar_time.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            404,
            f"режим для {instrument_id} на {timeframe} ещё не рассчитан: "
            "нет закрытых баров или не отработал feature-engine",
        )
    return RegimeOut(
        bar_time=row.bar_time,
        timeframe=row.timeframe,
        trend=row.trend,
        trend_score=row.trend_score,
        volatility=row.volatility,
        liquidity=row.liquidity,
        derivatives_flow=row.derivatives_flow,
        detail=row.detail_json,
    )
