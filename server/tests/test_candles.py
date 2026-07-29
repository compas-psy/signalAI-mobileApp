"""Каноническая свеча и ресемпл: три запрета §4.4 предъявлены."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.market.candles import (
    MOEX_SESSION_START_HOUR_UTC,
    Candle,
    closed_only,
    detect_gaps,
    is_stale,
    resample_days_to,
    resample_hours,
)
from app.models.enums import QualityFlag, Timeframe


def hour(h: int, *, day: int = 1, close: str = "100", is_closed: bool = True,
         volume: str | None = "10", oi: str | None = None) -> Candle:
    """Часовая свеча с предсказуемыми уровнями."""
    price = Decimal(close)
    return Candle(
        open_time=datetime(2026, 7, day, h, tzinfo=UTC),
        open=price,
        high=price + 2,
        low=price - 2,
        close=price,
        volume_units=Decimal(volume) if volume is not None else None,
        open_interest=Decimal(oi) if oi is not None else None,
        is_closed=is_closed,
        source="test",
    )


# ─── Целостность свечи ────────────────────────────────────────────────────


def test_candle_requires_timezone():
    """§4.4: все времена в UTC. Наивное время — источник ошибок на сутки."""
    with pytest.raises(ValueError, match="с зоной"):
        Candle(
            open_time=datetime(2026, 7, 1, 10),
            open=Decimal(100), high=Decimal(101),
            low=Decimal(99), close=Decimal(100),
        )


def test_candle_rejects_inverted_range():
    with pytest.raises(ValueError, match="ниже low"):
        Candle(
            open_time=datetime(2026, 7, 1, 10, tzinfo=UTC),
            open=Decimal(100), high=Decimal(99),
            low=Decimal(101), close=Decimal(100),
        )


def test_candle_rejects_close_outside_range():
    with pytest.raises(ValueError, match="close вне диапазона"):
        Candle(
            open_time=datetime(2026, 7, 1, 10, tzinfo=UTC),
            open=Decimal(100), high=Decimal(101),
            low=Decimal(99), close=Decimal(105),
        )


def test_candle_geometry():
    c = Candle(
        open_time=datetime(2026, 7, 1, 10, tzinfo=UTC),
        open=Decimal(100), high=Decimal(110),
        low=Decimal(95), close=Decimal(108),
    )
    assert c.range == Decimal(15)
    assert c.body == Decimal(8)
    assert c.upper_wick == Decimal(2)
    assert c.lower_wick == Decimal(5)
    assert c.is_up and not c.is_down


# ─── Запрет 1: незакрытая свеча (§4.4) ────────────────────────────────────


def test_closed_only_drops_forming_anywhere():
    """Незакрытой может оказаться не только последняя свеча."""
    series = [hour(9), hour(10, is_closed=False), hour(11)]
    assert [c.open_time.hour for c in closed_only(series)] == [9, 11]


def test_resample_drops_incomplete_last_bucket():
    """Ведро из трёх часов вместо четырёх не выдаётся за закрытое 4H.

    Иначе структура «мерцает»: детектор находит слом, которого через час
    не будет.
    """
    series = [hour(h) for h in range(6, 13)]  # 06..12 UTC — 7 часов
    bars = resample_hours(series, 4, session_start_hour_utc=6)
    assert len(bars) == 1
    assert bars[0].open_time == datetime(2026, 7, 1, 6, tzinfo=UTC)
    assert bars[0].is_closed


def test_resample_can_keep_forming_bucket_but_marks_it():
    """Формирующееся ведро доступно явно — и помечено флагом."""
    series = [hour(h) for h in range(6, 13)]
    bars = resample_hours(series, 4, session_start_hour_utc=6, drop_forming=False)
    assert len(bars) == 2
    last = bars[-1]
    assert last.is_closed is False
    assert QualityFlag.PARTIAL_BAR.value in last.quality_flags


def test_bucket_with_unclosed_hour_is_not_closed():
    """Полное по числу часов ведро с незакрытым баром закрытым не считается."""
    series = [hour(6), hour(7), hour(8), hour(9, is_closed=False)]
    bars = resample_hours(series, 4, session_start_hour_utc=6, drop_forming=False)
    assert bars[0].is_closed is False


# ─── Запрет 2: ведро от открытия сессии ───────────────────────────────────


def test_moex_session_anchor_makes_full_first_bucket():
    """FORTS открывается в 09:00 МСК = 06:00 UTC.

    От полуночи первое ведро дня было бы огрызком, и ATR на 4H занижался бы
    систематически.
    """
    series = [hour(h) for h in range(6, 14)]  # два полных ведра от 06:00
    anchored = resample_hours(series, 4, session_start_hour_utc=MOEX_SESSION_START_HOUR_UTC)
    assert [c.open_time.hour for c in anchored] == [6, 10]

    midnight = resample_hours(series, 4, session_start_hour_utc=0)
    # От полуночи границы 04:00/08:00/12:00. Ведро 04:00 собрано всего из двух
    # часов (06 и 07) — период оно прожило, но данные в нём неполные, и это
    # видно по флагу. Именно такой огрызок систематически занижает ATR.
    assert [c.open_time.hour for c in midnight] == [4, 8]
    assert QualityFlag.PARTIAL_BAR.value in midnight[0].quality_flags
    assert QualityFlag.PARTIAL_BAR.value not in midnight[1].quality_flags
    # А с якорем на сессию оба ведра полные.
    assert all(
        QualityFlag.PARTIAL_BAR.value not in c.quality_flags for c in anchored
    )


def test_bucket_boundaries_do_not_depend_on_calendar_day():
    """Ведро, пересекающее полночь, остаётся одним ведром."""
    series = [
        hour(22, day=1), hour(23, day=1),
        hour(0, day=2), hour(1, day=2),
    ]
    bars = resample_hours(series, 4, session_start_hour_utc=2)
    assert len(bars) == 1
    assert bars[0].open_time == datetime(2026, 7, 1, 22, tzinfo=UTC)


def test_ohlc_of_bucket_is_correct():
    series = [
        Candle(datetime(2026, 7, 1, 6, tzinfo=UTC), Decimal(100), Decimal(105),
               Decimal(98), Decimal(103), source="t"),
        Candle(datetime(2026, 7, 1, 7, tzinfo=UTC), Decimal(103), Decimal(110),
               Decimal(102), Decimal(104), source="t"),
        Candle(datetime(2026, 7, 1, 8, tzinfo=UTC), Decimal(104), Decimal(106),
               Decimal(95), Decimal(96), source="t"),
        Candle(datetime(2026, 7, 1, 9, tzinfo=UTC), Decimal(96), Decimal(99),
               Decimal(94), Decimal(97), source="t"),
    ]
    bar = resample_hours(series, 4, session_start_hour_utc=6)[0]
    assert bar.open == Decimal(100)   # открытие первой
    assert bar.high == Decimal(110)   # максимум по всем
    assert bar.low == Decimal(94)     # минимум по всем
    assert bar.close == Decimal(97)   # закрытие последней


# ─── Запрет 3: открытый интерес не выдумывается ───────────────────────────


def test_open_interest_is_last_known_not_sum():
    """OI — состояние на конец периода, а не поток за период.

    Сложение превратило бы 100 тысяч контрактов в 400 тысяч.
    """
    series = [
        hour(6, oi="100"), hour(7, oi="110"),
        hour(8, oi="120"), hour(9, oi="130"),
    ]
    bar = resample_hours(series, 4, session_start_hour_utc=6)[0]
    assert bar.open_interest == Decimal(130)
    assert bar.volume_units == Decimal(40)  # объём как раз складывается


def test_missing_open_interest_is_flagged_not_zeroed():
    """Нет OI ни в одной свече — ведро без OI и с флагом, а не с нулём."""
    series = [hour(h) for h in range(6, 10)]
    bar = resample_hours(series, 4, session_start_hour_utc=6)[0]
    assert bar.open_interest is None
    assert QualityFlag.OI_UNAVAILABLE.value in bar.quality_flags


def test_partial_open_interest_uses_last_available():
    series = [hour(6, oi="100"), hour(7), hour(8, oi="140"), hour(9)]
    bar = resample_hours(series, 4, session_start_hour_utc=6)[0]
    assert bar.open_interest == Decimal(140)
    assert QualityFlag.OI_UNAVAILABLE.value not in bar.quality_flags


def test_missing_volume_stays_none():
    series = [hour(h, volume=None) for h in range(6, 10)]
    bar = resample_hours(series, 4, session_start_hour_utc=6)[0]
    assert bar.volume_units is None


def test_quality_flags_propagate_upward():
    """Флаг младшей свечи не теряется при склейке."""
    dirty = hour(7).with_flag(QualityFlag.OUTLIER)
    series = [hour(6), dirty, hour(8), hour(9)]
    bar = resample_hours(series, 4, session_start_hour_utc=6)[0]
    assert QualityFlag.OUTLIER.value in bar.quality_flags


# ─── Недели и месяцы ──────────────────────────────────────────────────────


def day_candle(d: int, month: int = 7, close: str = "100") -> Candle:
    price = Decimal(close)
    return Candle(
        open_time=datetime(2026, month, d, tzinfo=UTC),
        open=price, high=price + 1, low=price - 1, close=price, source="t",
    )


def test_weeks_are_grouped_from_monday():
    # 2026-07-06 — понедельник.
    series = [day_candle(d) for d in range(6, 18)]
    weeks = resample_days_to(series, "week")
    assert weeks[0].open_time == datetime(2026, 7, 6, tzinfo=UTC)
    assert weeks[1].open_time == datetime(2026, 7, 13, tzinfo=UTC)


def test_months_are_grouped_by_calendar_month():
    series = [day_candle(d, month=6) for d in range(1, 25)]
    series += [day_candle(d, month=7) for d in range(1, 25)]
    months = resample_days_to(series, "month")
    assert [m.open_time.month for m in months] == [6, 7]


def test_short_last_week_is_dropped_by_default():
    """Неполная последняя неделя не выдаётся за закрытую."""
    series = [day_candle(d) for d in range(6, 11)]  # пн–пт
    series += [day_candle(13), day_candle(14)]      # только пн–вт следующей
    weeks = resample_days_to(series, "week")
    assert len(weeks) == 1


def test_invalid_period_is_rejected():
    with pytest.raises(ValueError, match="week"):
        resample_days_to([day_candle(6)], "quarter")


# ─── Дыры и устаревание ───────────────────────────────────────────────────


def test_gap_is_detected():
    series = [hour(6), hour(7), hour(10)]
    holes = detect_gaps(series, Timeframe.H1)
    assert len(holes) == 1
    assert holes[0][0].hour == 7 and holes[0][1].hour == 10


def test_no_gap_in_continuous_series():
    assert detect_gaps([hour(h) for h in range(6, 12)], Timeframe.H1) == []


def test_staleness_counts_from_bar_end_not_start():
    """Часовая свеча, открытая час назад, только что закрылась — она свежая."""
    series = [hour(10)]
    just_closed = datetime(2026, 7, 1, 11, 0, 30, tzinfo=UTC)
    assert not is_stale(series, Timeframe.H1, just_closed, max_age_seconds=300)

    much_later = datetime(2026, 7, 1, 12, tzinfo=UTC)
    assert is_stale(series, Timeframe.H1, much_later, max_age_seconds=300)


def test_empty_series_is_stale():
    """Нет данных — это не «свежие данные»."""
    assert is_stale([], Timeframe.H1, datetime.now(UTC), max_age_seconds=300)
