"""Owner-facing live chart data.

Signal generation remains reproducible and reads only persisted closed bars.
This route serves a different question: "where is the market now relative to
the immutable idea?" It therefore overlays the exchange's current candle on
top of audited history, marks that candle DISPLAY_ONLY, and never persists it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...market import crypto, moex
from ...market.moex_bar_guard import guarded_candles, is_time_closed
from ...models import Bar, Instrument
from ...models.enums import AssetClass, Timeframe, Venue
from ...schemas.common import ApiModel, Money

router = APIRouter(tags=["market"])


class DisplayBar(ApiModel):
    open_time: datetime
    open: Money
    high: Money
    low: Money
    close: Money
    volume_units: Money | None = None
    volume_notional: Money | None = None
    is_closed: bool
    source: str
    quality_flags: list[str] = Field(default_factory=list)


class DisplayBars(ApiModel):
    instrument_id: str
    symbol: str
    timeframe: Timeframe
    generated_at: datetime
    live_overlay: bool
    bars: list[DisplayBar] = Field(default_factory=list)


def _bar(row: Bar) -> DisplayBar:
    return DisplayBar(
        open_time=row.open_time,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume_units=row.volume_units,
        volume_notional=row.volume_notional,
        is_closed=bool(row.is_closed),
        source=str(row.source),
        quality_flags=list(row.quality_flags or []),
    )


def _candle(candle, *, source: str) -> DisplayBar:
    flags = list(candle.quality_flags or ())
    if not candle.is_closed and "DISPLAY_ONLY" not in flags:
        flags.append("DISPLAY_ONLY")
    return DisplayBar(
        open_time=candle.open_time,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume_units=candle.volume_units,
        volume_notional=candle.volume_notional,
        is_closed=bool(candle.is_closed),
        source=source if not candle.is_closed else str(candle.source),
        quality_flags=flags,
    )


def _require(session: Session, instrument_id: str) -> Instrument:
    item = session.execute(
        select(Instrument).where(Instrument.instrument_id == instrument_id)
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, f"инструмент {instrument_id} не найден")
    return item


def _minutes(timeframe: Timeframe) -> int:
    return {
        Timeframe.M10: 10,
        Timeframe.M15: 15,
        Timeframe.H1: 60,
        Timeframe.H4: 240,
        Timeframe.D1: 1440,
    }.get(timeframe, 60)


def _moex_live(
    instrument: Instrument,
    timeframe: Timeframe,
    *,
    limit: int,
    now: datetime,
):
    # ISS exposes 10m/1h/1d candles. H4 remains an analytical aggregation in
    # the engine and therefore has no direct live overlay; the caller still
    # gets persisted H4 history if it exists.
    if timeframe not in (Timeframe.M10, Timeframe.H1, Timeframe.D1):
        return []
    days = max(2, int(_minutes(timeframe) * min(limit, 220) / 1440) + 2)
    path = moex.FORTS
    if instrument.asset_class is not AssetClass.FUTURES:
        meta = instrument.metadata_json or {}
        market, board = meta.get("market"), meta.get("board")
        if market and board:
            path = f"engines/stock/markets/{market}/boards/{board}"
        else:
            path = moex.SHARES
    candles, _ = guarded_candles(
        instrument.symbol,
        timeframe,
        (now - timedelta(days=days)).date(),
        path=path,
    )
    return candles[-min(limit, 220):]


@router.get(
    "/market/{instrument_id:path}/display-bars",
    response_model=DisplayBars,
)
def display_bars(
    instrument_id: str,
    timeframe: Timeframe = Query(Timeframe.H1),
    limit: int = Query(200, ge=20, le=500),
    db: Session = Depends(get_db),
) -> DisplayBars:
    """Audited history plus a non-persisted live exchange tail.

    A live bar is evidence for the UI only. It is never written to PostgreSQL
    and therefore cannot change a signal, score, backtest or model label.
    """
    now = datetime.now(UTC)
    instrument = _require(db, instrument_id)

    def persisted_rows(selected_timeframe: Timeframe) -> list[Bar]:
        return list(
            reversed(
                list(
                    db.execute(
                        select(Bar)
                        .where(
                            Bar.instrument_id == instrument_id,
                            Bar.timeframe == selected_timeframe,
                            Bar.is_closed.is_(True),
                        )
                        .order_by(Bar.open_time.desc())
                        .limit(limit)
                    ).scalars()
                )
            )
        )

    def merge_persisted(
        target: dict[datetime, DisplayBar],
        rows: list[Bar],
        selected_timeframe: Timeframe,
    ) -> None:
        for row in rows:
            # Defensive read-time guard for rows written by an older server build.
            if instrument.venue is Venue.MOEX:
                shadow = type(
                    "Shadow",
                    (),
                    {
                        "open_time": row.open_time,
                        "is_closed": True,
                    },
                )()
                if not is_time_closed(shadow, selected_timeframe, now=now):
                    continue
            target[row.open_time] = _bar(row)

    merged: dict[datetime, DisplayBar] = {}
    merge_persisted(merged, persisted_rows(timeframe), timeframe)

    response_timeframe = timeframe
    live_overlay = False
    try:
        if instrument.venue is Venue.MOEX:
            tail = _moex_live(instrument, timeframe, limit=limit, now=now)
            for candle in tail:
                merged[candle.open_time] = _candle(
                    candle,
                    source="moex-live-display",
                )
            live_overlay = bool(tail)
        elif instrument.venue is Venue.CRYPTO:
            tail, _ = crypto.klines(
                instrument.symbol,
                timeframe,
                limit=min(limit, 20),
            )
            for candle in tail:
                merged[candle.open_time] = _candle(
                    candle,
                    source="bybit-live-display",
                )
            live_overlay = bool(tail)
    except Exception:
        # A chart must degrade to audited history, never disappear because the
        # exchange's live endpoint had a transient failure.
        live_overlay = False

    # MOEX ISS has no native H4 candles. H4 is useful for the analytical
    # engine, but an owner-facing display chart must not disappear just because
    # neither audited H4 history nor a native live H4 tail is available. In
    # that narrow case degrade to H1 and report the actual timeframe returned.
    if instrument.venue is Venue.MOEX and timeframe is Timeframe.H4 and not merged:
        fallback: dict[datetime, DisplayBar] = {}
        merge_persisted(fallback, persisted_rows(Timeframe.H1), Timeframe.H1)
        fallback_live = False
        try:
            tail = _moex_live(
                instrument,
                Timeframe.H1,
                limit=limit,
                now=now,
            )
            for candle in tail:
                fallback[candle.open_time] = _candle(
                    candle,
                    source="moex-live-display",
                )
            fallback_live = bool(tail)
        except Exception:
            fallback_live = False

        if fallback:
            merged = fallback
            response_timeframe = Timeframe.H1
            live_overlay = fallback_live

    rows = [merged[key] for key in sorted(merged)]
    return DisplayBars(
        instrument_id=instrument_id,
        symbol=instrument.symbol,
        timeframe=response_timeframe,
        generated_at=now,
        live_overlay=live_overlay,
        bars=rows[-limit:],
    )
