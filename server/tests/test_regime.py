"""Классификатор режима §9: четыре измерения, которые нельзя схлопнуть."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.market.candles import Candle
from app.models.enums import (
    DerivativesFlow,
    LiquidityRegime,
    TrendRegime,
    VolatilityRegime,
)
from app.regime.classifier import (
    classify_derivatives_flow,
    classify_liquidity,
    classify_trend,
    classify_volatility,
)

START = datetime(2026, 7, 1, tzinfo=UTC)


def rising(n: int = 260, step: Decimal = Decimal(2)) -> list[Candle]:
    out = []
    price = Decimal(100)
    for i in range(n):
        out.append(
            Candle(START + timedelta(hours=i), price, price + 2, price - 1,
                   price + 1, volume_units=Decimal(100), source="t")
        )
        price += step
    return out


def falling(n: int = 260) -> list[Candle]:
    out = []
    price = Decimal(1000)
    for i in range(n):
        out.append(
            Candle(START + timedelta(hours=i), price, price + 1, price - 2,
                   price - 1, volume_units=Decimal(100), source="t")
        )
        price -= Decimal(2)
    return out


def choppy(n: int = 260) -> list[Candle]:
    out = []
    for i in range(n):
        base = Decimal(100) + (Decimal(1) if i % 2 else Decimal(0))
        out.append(
            Candle(START + timedelta(hours=i), base, base + Decimal("0.5"),
                   base - Decimal("0.5"), base, volume_units=Decimal(100), source="t")
        )
    return out


# ─── Тренд §9.1 ───────────────────────────────────────────────────────────


def test_uptrend_is_detected():
    regime, score, signals, _ = classify_trend(rising())
    assert regime is TrendRegime.UPTREND
    assert score >= 3
    assert all(-1 <= s.vote <= 1 for s in signals)


def test_downtrend_is_detected():
    regime, score, _, _ = classify_trend(falling())
    assert regime is TrendRegime.DOWNTREND
    assert score <= -3


def test_chop_is_range_not_weak_trend():
    """Флэт — отдельное состояние, а не «слабый тренд»: в нём другой сетап."""
    regime, score, _, detail = classify_trend(choppy())
    assert regime is TrendRegime.RANGE
    assert abs(score) <= 1
    assert detail["adx"] < 20


def test_score_is_bounded():
    _, score, _, _ = classify_trend(rising(400, Decimal(5)))
    assert -5 <= score <= 5


def test_every_signal_is_reported_separately():
    """В разборе идеи должно быть видно, какой именно признак не выполнен."""
    _, _, signals, _ = classify_trend(rising())
    names = {s.name for s in signals}
    assert names == {
        "close_vs_ema_fast",
        "ema_order",
        "ema_slope",
        "adx",
        "structure",
    }
    assert all(s.detail for s in signals)


def test_unmeasured_signal_votes_zero_and_says_so():
    """«Не измерено» — не то же, что «нейтрально».

    В первом случае рынок спокоен, во втором мы просто не знаем.
    """
    short = rising(30)
    _, _, signals, _ = classify_trend(short)
    slow = next(s for s in signals if s.name == "ema_order")
    assert slow.vote == 0
    assert "не измерено" in slow.detail


def test_adx_amplifies_but_does_not_vote_alone():
    """ADX не имеет направления: усиливать нечего, когда мнения нет."""
    _, _, signals, _ = classify_trend(choppy())
    adx_signal = next(s for s in signals if s.name == "adx")
    assert adx_signal.vote == 0


def test_transition_when_signals_disagree():
    """Смешанные признаки — это TRANSITION, а не натянутый тренд."""
    up = rising(210)
    # Разворот от той же цены, без разрыва: иначе это не смена режима, а
    # дыра в данных.
    price = up[-1].close
    down = []
    for i in range(60):
        down.append(
            Candle(START + timedelta(hours=210 + i), price, price + 1,
                   price - 2, price - 1, volume_units=Decimal(100), source="t")
        )
        price -= Decimal(2)
    regime, score, _, _ = classify_trend(up + down)
    assert regime in (TrendRegime.TRANSITION, TrendRegime.DOWNTREND, TrendRegime.RANGE)


def test_deadband_prevents_phantom_trend_in_flat_market():
    """Мёртвая зона: разница в тысячную — не тренд.

    Без неё EMA50 обгоняет EMA200 на пренебрежимую величину, три признака
    голосуют «вверх», и движок выдаёт трендовый сетап в диапазоне.
    """
    flat = choppy()
    with_deadband, score_on, _, _ = classify_trend(flat)
    without, score_off, _, _ = classify_trend(flat, deadband_atr=0.0)
    assert with_deadband is TrendRegime.RANGE
    assert abs(score_on) < abs(score_off)


# ─── Волатильность §9.2 ───────────────────────────────────────────────────


def test_volatility_is_normalised_by_price():
    """Иначе «высокая волатильность» означала бы просто «дорогой инструмент».

    Два ряда с одинаковым относительным размахом обязаны получить одинаковый
    режим, независимо от абсолютной цены.
    """
    cheap = [
        Candle(START + timedelta(hours=i), Decimal(100), Decimal(102),
               Decimal(98), Decimal(100), source="t")
        for i in range(300)
    ]
    pricey = [
        Candle(START + timedelta(hours=i), Decimal(100000), Decimal(102000),
               Decimal(98000), Decimal(100000), source="t")
        for i in range(300)
    ]
    assert classify_volatility(cheap)[0] is classify_volatility(pricey)[0]


def test_volatility_spike_is_extreme():
    calm = [
        Candle(START + timedelta(hours=i), Decimal(100), Decimal("100.5"),
               Decimal("99.5"), Decimal(100), source="t")
        for i in range(280)
    ]
    storm = [
        Candle(START + timedelta(hours=280 + i), Decimal(100), Decimal(130),
               Decimal(70), Decimal(100), source="t")
        for i in range(20)
    ]
    regime, rank = classify_volatility(calm + storm)
    assert regime is VolatilityRegime.EXTREME
    assert rank > 90


def test_volatility_without_history_is_normal_and_says_nothing():
    regime, rank = classify_volatility([])
    assert regime is VolatilityRegime.NORMAL
    assert rank is None


# ─── Ликвидность §9.3 ─────────────────────────────────────────────────────


COMMON = dict(min_notional=Decimal(100_000_000), max_spread=0.0015)


def test_good_liquidity():
    regime, reasons = classify_liquidity(
        relative_spread=0.0005, median_notional=Decimal(500_000_000), **COMMON
    )
    assert regime is LiquidityRegime.GOOD
    assert reasons == []


def test_thin_liquidity_explains_itself():
    """«Тонко» без объяснения не позволяет ни поспорить, ни починить."""
    regime, reasons = classify_liquidity(
        relative_spread=0.003, median_notional=Decimal(50_000_000), **COMMON
    )
    assert regime is LiquidityRegime.THIN
    assert any("спред" in r for r in reasons)
    assert any("оборот" in r for r in reasons)


def test_untradeable_on_wide_spread():
    regime, reasons = classify_liquidity(
        relative_spread=0.01, median_notional=Decimal(500_000_000), **COMMON
    )
    assert regime is LiquidityRegime.UNTRADEABLE
    assert "втрое выше" in reasons[0]


def test_closed_session_is_untradeable():
    regime, reasons = classify_liquidity(
        relative_spread=0.0001, median_notional=Decimal(10**12),
        session_open=False, **COMMON
    )
    assert regime is LiquidityRegime.UNTRADEABLE
    assert reasons == ["сессия закрыта"]


def test_missing_data_is_untradeable_not_normal():
    """Торговать вслепую нельзя: отсутствие данных не «нормальная ликвидность»."""
    regime, reasons = classify_liquidity(
        relative_spread=None, median_notional=None, **COMMON
    )
    assert regime is LiquidityRegime.UNTRADEABLE
    assert "нет данных" in reasons[0]


def test_gaps_lower_the_verdict():
    without = classify_liquidity(
        relative_spread=0.0005, median_notional=Decimal(500_000_000), **COMMON
    )[0]
    with_gaps = classify_liquidity(
        relative_spread=0.0005, median_notional=Decimal(500_000_000),
        gaps=3, **COMMON
    )[0]
    assert without is LiquidityRegime.GOOD
    assert with_gaps is LiquidityRegime.NORMAL


# ─── Поток производных §9.4 ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("price_z", "oi_z", "expected"),
    [
        (2.0, 2.0, DerivativesFlow.LONG_BUILDUP),
        (-2.0, 2.0, DerivativesFlow.SHORT_BUILDUP),
        (2.0, -2.0, DerivativesFlow.SHORT_COVERING),
        (-2.0, -2.0, DerivativesFlow.LONG_LIQUIDATION),
        (0.1, 2.0, DerivativesFlow.NEUTRAL),
        (2.0, 0.1, DerivativesFlow.NEUTRAL),
    ],
)
def test_quadrants(price_z, oi_z, expected):
    assert classify_derivatives_flow(price_change_z=price_z, oi_change_z=oi_z) is expected


def test_missing_oi_is_neutral_and_must_be_marked_unmeasured():
    """Без OI ответ NEUTRAL означает «не измерено».

    Фактор производных в оценке обязан быть помечен неизмеренным, иначе
    отсутствие данных превратится в подтверждение.
    """
    assert (
        classify_derivatives_flow(price_change_z=2.0, oi_change_z=None)
        is DerivativesFlow.NEUTRAL
    )
