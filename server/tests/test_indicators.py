"""Индикаторы и структура: главное здесь — отсутствие взгляда в будущее."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.features.indicators import (
    adx,
    atr,
    ema,
    percentile_rank,
    relative_volume,
    rsi,
    structure_direction,
    swings,
    swings_known_at,
    true_range,
    zscore,
)
from app.market.candles import Candle

START = datetime(2026, 7, 1, tzinfo=UTC)


def bar(i: int, high: str, low: str, close: str | None = None,
        open_: str | None = None, volume: str = "100") -> Candle:
    h, low_ = Decimal(high), Decimal(low)
    c = Decimal(close) if close is not None else (h + low_) / 2
    o = Decimal(open_) if open_ is not None else c
    return Candle(
        open_time=START + timedelta(hours=i),
        open=o, high=h, low=low_, close=c,
        volume_units=Decimal(volume), source="t",
    )


def series(prices: list[tuple[str, str]], **kw) -> list[Candle]:
    return [bar(i, h, low, **kw) for i, (h, low) in enumerate(prices)]


# ─── Скользящие ───────────────────────────────────────────────────────────


def test_ema_starts_with_simple_average_not_first_price():
    """Старт с первой цены десятки баров показывал бы почти саму цену."""
    values = [Decimal(x) for x in (10, 20, 30, 40, 50)]
    out = ema(values, 3)
    assert out[:2] == [None, None]
    assert out[2] == Decimal(20)  # (10+20+30)/3
    # Дальше рекуррентно: (40-20)*0.5+20 = 30
    assert out[3] == Decimal(30)


def test_ema_returns_nones_when_history_too_short():
    assert ema([Decimal(1), Decimal(2)], 5) == [None, None]


def test_ema_rejects_zero_period():
    with pytest.raises(ValueError):
        ema([Decimal(1)], 0)


def test_true_range_uses_previous_close():
    candles = [
        bar(0, "105", "95", "100"),
        bar(1, "108", "104", "106"),   # гэп вверх: TR считается от 100
    ]
    tr = true_range(candles)
    assert tr[0] == Decimal(10)
    assert tr[1] == Decimal(8)  # max(4, |108-100|=8, |104-100|=4)


def test_atr_uses_wilder_smoothing():
    """ATR по Уайлдеру, а не EMA: от него зависит буфер стопа (§10.4)."""
    candles = [bar(i, "110", "100", "105") for i in range(20)]
    values = atr(candles, 14)
    assert values[12] is None
    assert values[13] == Decimal(10)
    # Ряд постоянного размаха — ATR остаётся равным ему.
    assert values[19] == Decimal(10)


def test_atr_is_decimal_because_it_becomes_a_stop():
    candles = [bar(i, "110", "100", "105") for i in range(20)]
    assert isinstance(atr(candles, 14)[-1], Decimal)


def test_rsi_saturates_on_monotone_growth():
    candles = [bar(i, str(100 + i), str(99 + i), str(100 + i)) for i in range(30)]
    assert rsi(candles, 14)[-1] == pytest.approx(100.0)


def test_rsi_is_neutral_on_flat_series():
    candles = [bar(i, "101", "99", "100") for i in range(30)]
    values = rsi(candles, 14)
    assert values[-1] == pytest.approx(100.0) or values[-1] == pytest.approx(50.0)


def test_adx_is_high_in_strong_trend_and_low_in_chop():
    trend = [bar(i, str(100 + 2 * i), str(98 + 2 * i), str(99 + 2 * i))
             for i in range(60)]
    chop = [bar(i, "102" if i % 2 else "101", "99" if i % 2 else "100",
                "100.5" if i % 2 else "100") for i in range(60)]
    assert adx(trend, 14)[-1] > 40
    assert adx(chop, 14)[-1] < 25


def test_adx_returns_none_without_enough_history():
    assert all(v is None for v in adx([bar(i, "101", "99") for i in range(10)], 14))


# ─── Свинги: подтверждение и запрет заглядывания вперёд ───────────────────


def test_swing_is_not_reported_without_confirmation_bars():
    """§8.3: экстремум без k баров справа не возвращается вовсе.

    Не «возвращается с пометкой»: помеченным значением обязательно
    кто-нибудь воспользуется.
    """
    # Максимум на индексе 3, справа только один бар.
    candles = series([("100", "98"), ("102", "99"), ("104", "101"),
                      ("110", "105"), ("108", "104")])
    assert swings(candles, confirm_bars=2) == []


def test_swing_appears_once_confirmation_arrives():
    candles = series([("100", "98"), ("102", "99"), ("110", "105"),
                      ("108", "104"), ("106", "103"), ("105", "102")])
    found = swings(candles, confirm_bars=2)
    assert len(found) == 1
    high = found[0]
    assert high.is_high and high.index == 2
    # Узнали о нём только на баре 4.
    assert high.confirmed_index == 4
    assert high.price == Decimal(110)


def test_known_at_hides_future_swings():
    """Прогон по истории обязан видеть только подтверждённое к этому бару."""
    candles = series([("100", "98"), ("102", "99"), ("110", "105"),
                      ("108", "104"), ("106", "103"), ("104", "100"),
                      ("103", "95"), ("105", "99"), ("107", "101")])
    found = swings(candles, confirm_bars=2)
    assert swings_known_at(found, 3) == []
    assert len(swings_known_at(found, 4)) == 1
    assert len(swings_known_at(found, 8)) >= 1


def test_min_move_filters_noise():
    """Мелкие зубцы на 4H дают шум, из которого не строится структура."""
    candles = series([
        ("100", "98"), ("101", "99"), ("110", "105"), ("108", "104"),
        ("109", "103"), ("108.5", "104"), ("109.2", "104.5"),
        ("120", "112"), ("118", "110"), ("117", "109"),
    ])
    noisy = swings(candles, confirm_bars=2)
    clean = swings(candles, confirm_bars=2, min_move=Decimal(5))
    assert len(clean) <= len(noisy)


def test_structure_direction_needs_two_pairs():
    candles = series([("100", "98"), ("110", "105"), ("108", "104")])
    assert structure_direction(swings(candles, confirm_bars=1)) == "flat"


def test_structure_direction_detects_uptrend():
    candles = series([
        ("100", "95"), ("110", "104"), ("105", "99"),
        ("120", "114"), ("115", "108"), ("130", "124"), ("128", "120"),
    ])
    assert structure_direction(swings(candles, confirm_bars=1)) == "up"


def test_structure_direction_detects_downtrend():
    candles = series([
        ("130", "124"), ("120", "114"), ("125", "119"),
        ("110", "104"), ("115", "109"), ("100", "94"), ("103", "97"),
    ])
    assert structure_direction(swings(candles, confirm_bars=1)) == "down"


# ─── Объём ────────────────────────────────────────────────────────────────


def test_relative_volume_uses_median_not_mean():
    """Один климакс не должен обесценивать шкалу на всё окно вперёд."""
    candles = [bar(i, "101", "99", volume="100") for i in range(19)]
    candles.append(bar(19, "101", "99", volume="10000"))   # климакс
    candles.append(bar(20, "101", "99", volume="200"))     # вдвое выше нормы

    values = relative_volume(candles, window=20)
    # По медиане следующий бар честно вдвое выше нормы. По среднему он был бы
    # ниже единицы — «нормальный объём» после климакса.
    assert values[20] == pytest.approx(2.0, abs=0.1)


def test_relative_volume_is_none_without_window():
    candles = [bar(i, "101", "99") for i in range(5)]
    assert relative_volume(candles, window=20) == [None] * 5


def test_zscore_handles_flat_series():
    """Нулевое отклонение — это ноль, а не деление на ноль."""
    assert zscore([5.0] * 25, window=20)[-1] == 0.0


def test_zscore_marks_outlier():
    values = [1.0] * 24 + [10.0]
    assert zscore(values, window=20)[-1] > 3


def test_percentile_rank_bounds():
    values = [float(i) for i in range(100)]
    assert percentile_rank(values, -1) == 0.0
    assert percentile_rank(values, 200) == 100.0
    assert 45 < percentile_rank(values, 50) < 55


def test_percentile_rank_on_empty_is_neutral():
    assert percentile_rank([], 5.0) == 50.0
