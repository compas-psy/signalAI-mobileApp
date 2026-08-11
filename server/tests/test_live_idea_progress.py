from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from app.api.v1.idea_progress import evaluate_forts_progress
from app.market.candles import Candle
from app.market.moex_bar_guard import is_time_closed
from app.models.enums import Direction, Timeframe


def _candle(hour: int, minute: int, high: int, low: int, close: int, *, closed=True):
    mid = (high + low) // 2
    return Candle(
        open_time=datetime(2026, 8, 11, hour, minute, tzinfo=UTC),
        open=Decimal(mid),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        is_closed=closed,
        source="test",
    )


def _pxu6(signal_minute: int = 27):
    return SimpleNamespace(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        instrument_id="MOEX:FUT:PXU6",
        signal_time=datetime(2026, 8, 11, 5, signal_minute, tzinfo=UTC),
        direction=Direction.SHORT,
        entry_low=Decimal("13675"),
        entry_high=Decimal("13695"),
        entry_reference=Decimal("13685"),
        stop=Decimal("13790"),
        tp1=Decimal("13580"),
        tp2=Decimal("13475"),
        tp3=Decimal("13370"),
    )


def test_forming_h1_is_not_closed_until_hour_finishes():
    candle = _candle(5, 0, 13700, 13600, 13650)
    assert is_time_closed(
        candle,
        Timeframe.H1,
        now=datetime(2026, 8, 11, 5, 27, tzinfo=UTC),
    ) is False
    assert is_time_closed(
        candle,
        Timeframe.H1,
        now=datetime(2026, 8, 11, 6, 0, tzinfo=UTC),
    ) is True


def test_pxu6_confirms_entry_then_tp1_and_tp2_and_marks_new_entry_late():
    idea = _pxu6()
    instrument = SimpleNamespace(symbol="PXU6")
    candles = [
        # signal candle 08:20–08:30 MSK: no entry touch
        _candle(5, 20, 13670, 13620, 13640),
        # first complete post-signal candle: exact 13 685 traded
        _candle(5, 30, 13690, 13630, 13655),
        # TP1 traded
        _candle(5, 40, 13660, 13570, 13590),
        # TP2 traded
        _candle(5, 50, 13595, 13470, 13516, closed=False),
    ]

    result = evaluate_forts_progress(
        idea,
        instrument,
        candles,
        as_of=datetime(2026, 8, 11, 5, 57, tzinfo=UTC),
    )

    assert result.entry_was_available is True
    assert result.entry_at == datetime(2026, 8, 11, 5, 30, tzinfo=UTC)
    assert result.tp_hit_count == 2
    assert [hit.name for hit in result.target_hits] == ["TP1", "TP2"]
    assert result.status == "TP2_HIT"
    assert result.late is True
    assert result.entry_now_available is False
    assert result.current_price == Decimal("13516")
    assert result.current_bar_forming is True


def test_touch_inside_signal_candle_is_not_claimed_as_confirmed_entry():
    idea = _pxu6()
    instrument = SimpleNamespace(symbol="PXU6")
    candles = [
        # Signal appeared at 05:27 UTC, but this candle started at 05:20.
        # OHLC says 13 685 traded somewhere inside it, not whether before or
        # after 05:27. The app must say "ambiguous", not "entry was available".
        _candle(5, 20, 13690, 13620, 13650),
        _candle(5, 30, 13660, 13600, 13620),
    ]

    result = evaluate_forts_progress(
        idea,
        instrument,
        candles,
        as_of=datetime(2026, 8, 11, 5, 40, tzinfo=UTC),
    )

    assert result.entry_was_available is None
    assert result.ambiguous is True
    assert result.status == "ENTRY_AMBIGUOUS"
    assert "нельзя доказать" in result.summary


def test_target_before_any_entry_is_missed_not_profitable_trade():
    idea = _pxu6()
    instrument = SimpleNamespace(symbol="PXU6")
    candles = [
        _candle(5, 20, 13670, 13620, 13640),
        _candle(5, 30, 13660, 13570, 13590),
    ]

    result = evaluate_forts_progress(
        idea,
        instrument,
        candles,
        as_of=datetime(2026, 8, 11, 5, 40, tzinfo=UTC),
    )

    assert result.entry_was_available is False
    assert result.target_before_entry == "TP1"
    assert result.status == "MISSED_BEFORE_ENTRY"
    assert result.late is True
