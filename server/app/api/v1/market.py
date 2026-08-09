"""Рыночные данные (engine-ТЗ §23, блок Market)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db import get_db
from ...market import crypto
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
    """Состояние загрузки для текущего торгового множества.

    ``with_data`` и ``tradable`` обязаны иметь один знаменатель. Раньше первое
    считалось по всей исторической таблице инструментов, а второе — по текущему
    допуску; поэтому интерфейс честно, но бессмысленно показывал «307 из 14».
    История остаётся в ``instruments_total``, полнота — только про то, чем
    движок может торговать сейчас.
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
    for item in db.execute(select(Instrument).order_by(Instrument.instrument_id)).scalars():
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

    current = [r for r in rows if r.is_tradable and r.in_universe]
    if not current:
        current = [r for r in rows if r.is_tradable]

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

    current_latest = [r.last_bar_time for r in current if r.last_bar_time is not None]
    return DataStatusOut(
        server_time=now,
        instruments_total=len(rows),
        in_universe=sum(r.in_universe for r in rows),
        tradable=len(current),
        with_data=sum(1 for r in current if r.last_bar_time is not None),
        last_bar_time=max(current_latest) if current_latest else None,
        recent_quality_events=events,
        instruments=rows,
    )


def _require_instrument(db: Session, instrument_id: str) -> Instrument:
    item = db.execute(
        select(Instrument).where(Instrument.instrument_id == instrument_id)
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, f"инструмент {instrument_id} не найден")
    return item


def _bar_out(row) -> BarOut:
    return BarOut(
        open_time=row.open_time,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume_units=row.volume_units,
        volume_notional=row.volume_notional,
        open_interest=row.open_interest,
        is_closed=bool(row.is_closed),
        source=str(row.source),
        quality_flags=list(row.quality_flags or []),
    )


@router.get("/market/{instrument_id:path}/bars", response_model=list[BarOut])
def get_bars(
    instrument_id: str,
    timeframe: Timeframe = Query(...),
    limit: int = Query(300, ge=1, le=5000),
    closed_only: bool = Query(True),
    display_live: bool = Query(True),
    db: Session = Depends(get_db),
) -> list[BarOut]:
    """Chart bars plus a non-persisted live crypto overlay.

    Canonical calculations still use only closed rows from PostgreSQL. For an
    owner-facing chart, however, showing the last H1 close while DOGE has
    already crossed the entry is misleading. Crypto therefore gets a tiny
    direct Bybit tail on read. The forming candle is explicitly marked
    ``is_closed=false`` and never enters feature/scanner storage.
    """
    instrument = _require_instrument(db, instrument_id)
    stmt = (
        select(Bar)
        .where(Bar.instrument_id == instrument_id, Bar.timeframe == timeframe)
        .order_by(Bar.open_time.desc())
        .limit(limit)
    )
    if closed_only:
        stmt = stmt.where(Bar.is_closed.is_(True))
    persisted = list(reversed(list(db.execute(stmt).scalars())))
    merged: dict[datetime, BarOut] = {row.open_time: _bar_out(row) for row in persisted}

    if display_live and instrument.venue is Venue.CRYPTO:
        try:
            live, _ = crypto.klines(
                instrument.symbol,
                timeframe,
                limit=min(5, limit),
            )
        except Exception:
            # Chart freshness may degrade, but a transient Bybit read must not
            # hide the audited history already stored on the VPS.
            live = []
        for candle in live:
            item = _bar_out(candle)
            if not item.is_closed:
                item = item.model_copy(
                    update={
                        "source": "bybit-live-display",
                        "quality_flags": [*item.quality_flags, "DISPLAY_ONLY"],
                    }
                )
            merged[candle.open_time] = item

    rows = [merged[key] for key in sorted(merged)]
    return rows[-limit:]


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
