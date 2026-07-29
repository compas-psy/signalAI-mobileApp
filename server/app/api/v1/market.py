"""Рыночные данные (engine-ТЗ §23, блок Market)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Bar, DataQualityEvent, Instrument, RegimeSnapshot
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


class InstrumentDataOut(ApiModel):
    instrument_id: str
    symbol: str
    in_universe: bool
    is_tradable: bool
    universe_note: str
    daily_bars: int
    hourly_bars: int
    last_bar_time: datetime | None = None
    stale_hours: float | None = None


class DataStatusOut(ApiModel):
    """Состояние загрузки: чем живёт движок прямо сейчас.

    Нужен, чтобы «данные грузятся» было проверяемым утверждением. Пустая
    выдача идей без этого экрана неотличима от «сегодня нет сетапов», а это
    разные новости.
    """

    server_time: datetime
    instruments_total: int
    in_universe: int
    tradable: int
    with_data: int
    last_bar_time: datetime | None = None
    recent_quality_events: list[dict]
    instruments: list[InstrumentDataOut]


@router.get("/market/status", response_model=DataStatusOut)
def data_status(db: Session = Depends(get_db)) -> DataStatusOut:
    now = datetime.now(UTC)

    counts = {
        (row[0], row[1]): row[2]
        for row in db.execute(
            select(Bar.instrument_id, Bar.timeframe, func.count())
            .where(Bar.is_closed.is_(True))
            .group_by(Bar.instrument_id, Bar.timeframe)
        )
    }
    latest = {
        row[0]: row[1]
        for row in db.execute(
            select(Bar.instrument_id, func.max(Bar.open_time))
            .where(Bar.is_closed.is_(True))
            .group_by(Bar.instrument_id)
        )
    }

    rows: list[InstrumentDataOut] = []
    for item in db.execute(
        select(Instrument).order_by(Instrument.instrument_id)
    ).scalars():
        last = latest.get(item.instrument_id)
        rows.append(
            InstrumentDataOut(
                instrument_id=item.instrument_id,
                symbol=item.symbol,
                in_universe=item.in_universe,
                is_tradable=item.is_tradable,
                universe_note=item.universe_note,
                daily_bars=counts.get((item.instrument_id, Timeframe.D1), 0),
                hourly_bars=counts.get((item.instrument_id, Timeframe.H1), 0),
                last_bar_time=last,
                stale_hours=(
                    round((now - last).total_seconds() / 3600, 2)
                    if last is not None else None
                ),
            )
        )

    events = [
        {
            "at": e.occurred_at.isoformat(),
            "source": e.source,
            "instrument_id": e.instrument_id,
            "flag": e.flag,
            "detail": e.detail,
        }
        for e in db.execute(
            select(DataQualityEvent)
            .order_by(DataQualityEvent.occurred_at.desc())
            .limit(20)
        ).scalars()
    ]

    return DataStatusOut(
        server_time=now,
        instruments_total=len(rows),
        in_universe=sum(r.in_universe for r in rows),
        tradable=sum(r.is_tradable for r in rows),
        with_data=sum(1 for r in rows if r.last_bar_time is not None),
        last_bar_time=max(latest.values()) if latest else None,
        recent_quality_events=events,
        instruments=rows,
    )


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
