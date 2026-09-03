"""Fresh market-path admission for owner-approved paper entries.

An immutable TRIGGERED idea can still be a perfectly valid historical setup
while the execution opportunity has already gone.  This guard answers the
separate money-boundary question immediately before a new paper trade is
created: is the original entry still available to wait for, or has price
already traded through a target/stop so chasing the snapshot would be late?

FORTS is the first supported venue because it already has a trustworthy public
10-minute market-progress path.  Unsupported venues keep their existing flow
until they have an equivalent fresh source; FORTS itself fails closed when the
fresh path cannot be established.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import Instrument, TradeIdea
from ...models.enums import AssetClass, Venue
from . import idea_progress


def validate_fresh_forts_entry(
    db: Session,
    idea: TradeIdea,
    *,
    now: datetime | None = None,
) -> None:
    """Reject a new FORTS entry when the post-signal path has played out.

    The read-only ``market-progress`` endpoint and this approval guard use the
    same evaluator and the same guarded MOEX candles.  That keeps the red
    ``ВХОД ПОЗДНИЙ`` verdict on the phone and the server money boundary from
    drifting into two different truths.
    """

    instrument = db.execute(
        select(Instrument).where(Instrument.instrument_id == idea.instrument_id)
    ).scalar_one_or_none()
    if instrument is None:
        # A persisted idea without its instrument cannot be safely sized or
        # revalidated.  Approval is a money boundary, therefore fail closed.
        raise HTTPException(
            409,
            "не удалось подтвердить актуальность входа: инструмент идеи не найден",
        )

    if (
        instrument.venue is not Venue.MOEX
        or instrument.asset_class is not AssetClass.FUTURES
    ):
        return

    moment = now or datetime.now(UTC)
    since = (idea.signal_time - timedelta(days=1)).date()
    try:
        candles, _ = idea_progress.guarded_candles(
            instrument.symbol,
            idea_progress.Timeframe.M10,
            since,
            path=idea_progress.moex.FORTS,
        )
    except Exception as exc:
        raise HTTPException(
            503,
            "не удалось подтвердить актуальность входа по свежим данным FORTS: "
            f"{type(exc).__name__}",
        ) from None

    progress = idea_progress.evaluate_forts_progress(
        idea,
        instrument,
        candles,
        as_of=moment,
    )

    if progress.status == "NO_DATA":
        raise HTTPException(
            503,
            "не удалось подтвердить актуальность входа: после сигнала нет "
            "10-минутных данных FORTS",
        )

    if (
        progress.late
        or progress.stop_hit
        or progress.tp_hit_count > 0
        or progress.status == "MISSED_BEFORE_ENTRY"
    ):
        raise HTTPException(
            409,
            f"вход уже поздний — сделка упущена: {progress.summary}",
        )


__all__ = ["validate_fresh_forts_entry"]
