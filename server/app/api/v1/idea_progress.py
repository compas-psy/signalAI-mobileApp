"""Live market progress for an immutable trade idea.

The idea itself never changes. This endpoint answers the separate execution
question the owner has after a signal appears: was the planned entry actually
available after the signal, what targets have already traded, and is a new
entry now too late?

FORTS is evaluated on public MOEX 10-minute candles. Ten minutes is the finest
candle interval used by the existing ISS adapter. If the signal is born inside
a 10-minute candle, touches inside that same candle are explicitly ambiguous:
OHLC cannot tell whether they happened before or after the signal timestamp.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...market import moex
from ...market.moex_bar_guard import guarded_candles
from ...models import Instrument, TradeIdea
from ...models.enums import AssetClass, Direction, Timeframe, Venue
from ...schemas.common import ApiModel, Money

router = APIRouter(tags=["ideas"])


class TargetHit(ApiModel):
    name: str
    price: Money
    at: datetime
    forming_bar: bool = False
    same_bar_as_entry: bool = False


class IdeaMarketProgress(ApiModel):
    idea_id: UUID
    instrument_id: str
    symbol: str
    resolution: str = "10m"
    as_of: datetime
    current_price: Money | None = None
    current_bar_forming: bool = False

    # null means the only observed touch was in the 10m candle already forming
    # when the signal was created; OHLC cannot prove whether it happened after
    # the signal. False means no entry touch at all, True is confirmed later.
    entry_was_available: bool | None = False
    entry_at: datetime | None = None
    entry_zone_seen: bool = False
    entry_now_available: bool = False

    target_hits: list[TargetHit] = Field(default_factory=list)
    tp_hit_count: int = 0
    target_before_entry: str = ""
    stop_hit: bool = False
    stop_at: datetime | None = None
    ambiguous: bool = False
    late: bool = False
    status: str
    summary: str


def _touch(candle, price: Decimal) -> bool:
    return candle.low <= price <= candle.high


def _zone_touch(candle, low: Decimal, high: Decimal) -> bool:
    return candle.high >= low and candle.low <= high


def _target_touched(candle, price: Decimal, direction: Direction) -> bool:
    return candle.low <= price if direction is Direction.SHORT else candle.high >= price


def _stop_touched(candle, price: Decimal, direction: Direction) -> bool:
    return candle.high >= price if direction is Direction.SHORT else candle.low <= price


def _price_in_entry_zone(price: Decimal, idea: TradeIdea) -> bool:
    return idea.entry_low <= price <= idea.entry_high


def evaluate_forts_progress(
    idea: TradeIdea,
    instrument: Instrument,
    candles: list,
    *,
    as_of: datetime,
) -> IdeaMarketProgress:
    """Evaluate a price path without changing the idea or its lifecycle."""
    relevant = [
        candle
        for candle in candles
        if candle.open_time + timedelta(minutes=10) > idea.signal_time
    ]
    if not relevant:
        return IdeaMarketProgress(
            idea_id=idea.id,
            instrument_id=idea.instrument_id,
            symbol=instrument.symbol,
            as_of=as_of,
            status="NO_DATA",
            summary="После сигнала ещё нет 10-минутных данных MOEX.",
        )

    targets = [
        ("TP1", idea.tp1),
        ("TP2", idea.tp2),
        *([("TP3", idea.tp3)] if idea.tp3 is not None else []),
    ]
    entry: bool | None = False
    entry_at: datetime | None = None
    entry_zone_seen = False
    hits: list[TargetHit] = []
    hit_names: set[str] = set()
    target_before_entry = ""
    stop_hit = False
    stop_at: datetime | None = None
    ambiguous = False

    for candle in relevant:
        signal_bar = candle.open_time < idea.signal_time
        forming = not candle.is_closed
        zone = _zone_touch(candle, idea.entry_low, idea.entry_high)
        exact_entry = _touch(candle, idea.entry_reference)
        entry_zone_seen = entry_zone_seen or zone

        if entry is not True:
            # A touch in the candle that already existed at signal birth is not
            # proof of post-signal availability. Preserve that uncertainty
            # instead of answering "yes" from pre-signal price action.
            if signal_bar and exact_entry:
                entry = None
                ambiguous = True
                continue

            touched_before = [
                (name, price)
                for name, price in targets
                if name not in hit_names and _target_touched(candle, price, idea.direction)
            ]
            if not exact_entry:
                if touched_before and not target_before_entry:
                    target_before_entry = touched_before[-1][0]
                    for name, price in touched_before:
                        hit_names.add(name)
                        hits.append(
                            TargetHit(
                                name=name,
                                price=price,
                                at=candle.open_time,
                                forming_bar=forming,
                            )
                        )
                continue

            # Full candle after the signal crossed the exact planned entry.
            entry = True
            entry_at = candle.open_time
            same_targets = [
                (name, price)
                for name, price in targets
                if name not in hit_names and _target_touched(candle, price, idea.direction)
            ]
            same_stop = _stop_touched(candle, idea.stop, idea.direction)
            if same_targets or same_stop:
                # Within one OHLC candle ordering is unknowable. We can say all
                # levels traded, but not pretend to know their sequence.
                ambiguous = True
            for name, price in same_targets:
                hit_names.add(name)
                hits.append(
                    TargetHit(
                        name=name,
                        price=price,
                        at=candle.open_time,
                        forming_bar=forming,
                        same_bar_as_entry=True,
                    )
                )
            if same_stop:
                stop_hit = True
                stop_at = candle.open_time
                break
            continue

        new_targets = [
            (name, price)
            for name, price in targets
            if name not in hit_names and _target_touched(candle, price, idea.direction)
        ]
        same_stop = _stop_touched(candle, idea.stop, idea.direction)
        if new_targets and same_stop:
            ambiguous = True
        for name, price in new_targets:
            hit_names.add(name)
            hits.append(
                TargetHit(
                    name=name,
                    price=price,
                    at=candle.open_time,
                    forming_bar=forming,
                )
            )
        if same_stop:
            stop_hit = True
            stop_at = candle.open_time
            break

    last = relevant[-1]
    current = last.close
    entry_now = _price_in_entry_zone(current, idea)
    tp_count = len(hit_names)
    late = bool(target_before_entry) or tp_count > 0 or stop_hit

    if stop_hit:
        status = "AMBIGUOUS" if ambiguous else "STOPPED"
        summary = (
            "Стоп торговался после входа; порядок уровней внутри одной 10m свечи неоднозначен."
            if ambiguous
            else "Вход по плану был доступен, затем рынок дошёл до стопа. Новый вход запрещён."
        )
    elif target_before_entry:
        status = "MISSED_BEFORE_ENTRY"
        summary = (
            f"Рынок дошёл до {target_before_entry} раньше подтверждённого касания входа. "
            "По 10m данным плановый вход не доказан; догонять движение поздно."
        )
    elif tp_count:
        status = f"TP{tp_count}_HIT"
        entered = "Вход по плану был доступен" if entry is True else "Касание входа по 10m неоднозначно"
        summary = f"{entered}; уже пройдено целей: {tp_count}. Новый вход поздний."
    elif entry is True:
        status = "ENTRY_AVAILABLE"
        summary = (
            "Плановая цена входа торговалась после появления сигнала. "
            + ("Цена сейчас снова в зоне входа." if entry_now else "Цель и стоп пока не достигнуты.")
        )
    elif entry is None:
        status = "ENTRY_AMBIGUOUS"
        summary = (
            "Цена входа была внутри 10m свечи, в которой появился сигнал. "
            "По OHLC нельзя доказать, было ли касание уже после сигнала."
        )
    else:
        status = "WAITING_ENTRY"
        summary = "После сигнала плановая цена входа ещё не торговалась."

    return IdeaMarketProgress(
        idea_id=idea.id,
        instrument_id=idea.instrument_id,
        symbol=instrument.symbol,
        as_of=as_of,
        current_price=current,
        current_bar_forming=not last.is_closed,
        entry_was_available=entry,
        entry_at=entry_at,
        entry_zone_seen=entry_zone_seen,
        entry_now_available=entry_now and not late,
        target_hits=hits,
        tp_hit_count=tp_count,
        target_before_entry=target_before_entry,
        stop_hit=stop_hit,
        stop_at=stop_at,
        ambiguous=ambiguous,
        late=late,
        status=status,
        summary=summary,
    )


@router.get(
    "/ideas/{idea_id}/market-progress",
    response_model=IdeaMarketProgress,
)
def market_progress(
    idea_id: UUID,
    db: Session = Depends(get_db),
) -> IdeaMarketProgress:
    idea = db.get(TradeIdea, idea_id)
    if idea is None:
        raise HTTPException(404, "идея не найдена")
    instrument = db.execute(
        select(Instrument).where(Instrument.instrument_id == idea.instrument_id)
    ).scalar_one_or_none()
    if instrument is None:
        raise HTTPException(404, "инструмент идеи не найден")
    if instrument.venue is not Venue.MOEX or instrument.asset_class is not AssetClass.FUTURES:
        raise HTTPException(422, "live progress пока рассчитывается для FORTS")

    now = datetime.now(UTC)
    since = (idea.signal_time - timedelta(days=1)).date()
    try:
        candles, _ = guarded_candles(
            instrument.symbol,
            Timeframe.M10,
            since,
            path=moex.FORTS,
        )
    except Exception as exc:
        raise HTTPException(
            503,
            f"MOEX не отдала 10m путь после сигнала: {type(exc).__name__}: {exc}",
        ) from None
    return evaluate_forts_progress(idea, instrument, candles, as_of=now)


__all__ = ["IdeaMarketProgress", "evaluate_forts_progress", "market_progress"]
