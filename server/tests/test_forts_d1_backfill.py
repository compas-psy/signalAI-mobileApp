"""Регрессия: неполная D1-история FORTS должна сама достраиваться.

Живой runtime-funnel показал 1 164 пропуска FORTS: у каждого было 46
дневных баров при требуемых сканером 60. После появления хотя бы одного
бара старый incremental-код запрашивал только newest-5d, поэтому недостающая
старая история уже никогда не могла приехать.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.market.candles import Candle
from app.market.ingest import _since, store_candles
from app.models.enums import Timeframe


NOW = datetime(2026, 8, 25, 7, 0, tzinfo=UTC)


def _seed_daily(session, instrument_id: str, count: int) -> datetime:
    """Записать ``count`` закрытых дневок, последняя — вчера."""
    price = Decimal("100")
    candles = []
    for offset in range(count, 0, -1):
        candles.append(
            Candle(
                open_time=NOW - timedelta(days=offset),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume_units=Decimal("10"),
                is_closed=True,
                source="test",
            )
        )
    store_candles(session, instrument_id, Timeframe.D1, candles)
    return candles[-1].open_time


def test_shallow_forts_d1_history_requests_backfill_floor(session, instrument):
    """46 D1-баров не должны навечно застревать ниже scanner minimum=60."""
    _seed_daily(session, instrument.instrument_id, 46)

    since = _since(
        session,
        instrument,
        Timeframe.D1,
        moment=NOW,
        days=120,
    )

    assert since == (NOW - timedelta(days=120)).date()


def test_sufficient_forts_d1_history_stays_incremental(session, instrument):
    """После набора контекста сохраняем дешёвое 5-дневное перекрытие."""
    newest = _seed_daily(session, instrument.instrument_id, 60)

    since = _since(
        session,
        instrument,
        Timeframe.D1,
        moment=NOW,
        days=120,
    )

    assert since == (newest - timedelta(days=5)).date()
