"""Три стратегии §10–§12: каждое условие ТЗ снимается отдельно.

Форма теста одна: собираем контекст, где кандидат обязан появиться, затем
ломаем ровно одно условие ТЗ и убеждаемся, что кандидата нет **и что причина
названа**. Отбраковка без причины не позволяет отличить «рынок не дал» от
«движок сломался».
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.detectors.base import Reading
from app.detectors.smc import LiquidityPool, OrderBlock, StructureEvent, Sweep
from app.detectors.wyckoff import TradingRange, WyckoffEvent
from app.features.indicators import Swing
from app.market.candles import Candle
from app.models.enums import (
    Direction,
    LiquidityRegime,
    Strategy,
    TrendRegime,
    VolatilityRegime,
)
from app.regime.classifier import RegimeResult, TrendSignal
from app.strategies import breakout_retest, trend_pullback, wyckoff_reversal
from app.strategies.base import Candidate, Rejection, SetupContext

START = datetime(2026, 7, 1, tzinfo=UTC)


def bar(i: int, o: str, h: str, low: str, cl: str, vol: str = "100") -> Candle:
    return Candle(
        START + timedelta(hours=i), Decimal(o), Decimal(h), Decimal(low),
        Decimal(cl), volume_units=Decimal(vol), source="t",
    )


def regime(
    trend=TrendRegime.UPTREND, score=4, volatility=VolatilityRegime.NORMAL,
    liquidity=LiquidityRegime.GOOD, adx=25.0, structure="up",
) -> RegimeResult:
    return RegimeResult(
        trend=trend, trend_score=score, volatility=volatility, liquidity=liquidity,
        derivatives_flow="NEUTRAL",
        signals=[TrendSignal("structure", "Структура", 1, "выше максимумы")],
        detail={"adx": adx, "structure": structure},
    )


def reading(detector: str, kind: str, payload: dict) -> Reading:
    return Reading(
        detector=detector, version=f"{detector}-1.0.0", evidence_kind=kind,
        summary="", payload=payload,
    )


# ─── S1 TREND_PULLBACK §10 ────────────────────────────────────────────────


def pullback_context(**over) -> SetupContext:
    """Восходящий тренд, откат в order block, снятие ликвидности и триггер."""
    swings = [
        Swing(index=5, confirmed_index=7, price=Decimal(100), is_high=False),
        Swing(index=15, confirmed_index=17, price=Decimal(120), is_high=True),
    ]
    smc = reading(
        "smc", "smc_context",
        {
            "swings": swings,
            "order_blocks": [
                OrderBlock(index=18, low=Decimal(108), high=Decimal(112),
                           is_bullish=True, body_atr=1.2, volume_z=1.0, reacted=True)
            ],
            "fvg": [],
            "pools": [],
            "sweeps": [
                Sweep(index=20, swing_index=5, level=Decimal(110), extreme=Decimal(107),
                      penetration_atr=0.3, return_bars=0, is_high=False)
            ],
            "events": [
                StructureEvent("BOS", "up", Decimal(115), 21, 1.5, 15)
            ],
        },
    )
    pa = reading("price_action", "entry_trigger", {"direction": "long"})
    base = dict(
        instrument_id="MOEX:FUT:SIU6",
        context_bars=[bar(i, "100", "102", "98", "101") for i in range(50)],
        setup_bars=[bar(i, "100", "102", "98", "101") for i in range(50)],
        trigger_bars=[bar(i, "109", "111", "108", "110") for i in range(50)],
        context_tf="1d", setup_tf="4h", trigger_tf="1h",
        regime=regime(), horizon_days=5, tick_size=Decimal(1),
        smc=smc, price_action=pa,
        volume_reading=reading("volume", "volume_confirmation", {}),
        atr_setup=Decimal(4), atr_trigger=Decimal(2),
    )
    base.update(over)
    return SetupContext(**base)


def test_pullback_produces_candidate():
    result = trend_pullback.build(pullback_context())
    assert isinstance(result, Candidate)
    assert result.strategy is Strategy.TREND_PULLBACK
    assert result.direction is Direction.LONG
    assert result.stop < result.entry_low
    assert result.rr(1) >= Decimal(2)


def test_pullback_refuses_in_range_regime():
    """§10.1: контекст обязан быть трендом."""
    result = trend_pullback.build(pullback_context(regime=regime(trend=TrendRegime.RANGE)))
    assert isinstance(result, Rejection)
    assert "context_trend" in {c.name for c in result.failed}


def test_pullback_refuses_on_weak_trend_score():
    result = trend_pullback.build(pullback_context(regime=regime(score=2)))
    assert isinstance(result, Rejection)
    assert "trend_score" in {c.name for c in result.failed}


def test_pullback_refuses_in_extreme_volatility():
    """§10.1: стоп в экстремальной волатильности выбьет раньше идеи."""
    result = trend_pullback.build(
        pullback_context(regime=regime(volatility=VolatilityRegime.EXTREME))
    )
    assert isinstance(result, Rejection)
    assert "volatility" in {c.name for c in result.failed}


def test_pullback_refuses_when_untradeable():
    result = trend_pullback.build(
        pullback_context(regime=regime(liquidity=LiquidityRegime.UNTRADEABLE))
    )
    assert isinstance(result, Rejection)
    assert "tradable" in {c.name for c in result.failed}


def test_pullback_needs_two_zones():
    """§10.2: коррекция входит минимум в две зоны. Одна — не конфлюэнс."""
    ctx = pullback_context()
    empty = reading("smc", "smc_context", {**ctx.smc.payload, "order_blocks": []})
    result = trend_pullback.build(replace(ctx, smc=empty))
    assert isinstance(result, Rejection)
    assert "zone_confluence" in {c.name for c in result.failed}


def test_pullback_needs_two_triggers():
    """§10.3: минимум два подтверждения."""
    ctx = pullback_context()
    lonely = reading(
        "smc", "smc_context", {**ctx.smc.payload, "sweeps": [], "events": []}
    )
    result = trend_pullback.build(
        replace(ctx, smc=lonely, price_action=None, volume_reading=None)
    )
    assert isinstance(result, Rejection)
    assert "triggers" in {c.name for c in result.failed}


def test_pullback_rejects_too_deep_retracement():
    """§10.2: глубже 0.786 — это уже не откат."""
    deep = [bar(i, "101", "103", "100", "102") for i in range(50)]
    result = trend_pullback.build(pullback_context(trigger_bars=deep))
    assert isinstance(result, Rejection)
    assert "retracement_depth" in {c.name for c in result.failed}


def test_pullback_reports_counter_factors():
    """§32: факторы против обязательны."""
    result = trend_pullback.build(pullback_context())
    assert isinstance(result, Candidate)
    assert isinstance(result.counter_factors, tuple)


def test_pullback_uses_only_readings_it_relied_on():
    """§9.1: разметка собирается только из использованных наблюдений."""
    result = trend_pullback.build(pullback_context())
    assert all(r is not None for r in result.used)
    assert {r.detector for r in result.used} <= {"smc", "price_action", "volume", "oi"}


# ─── S2 BREAKOUT_RETEST §11 ───────────────────────────────────────────────


def breakout_context(**over) -> SetupContext:
    trading_range = TradingRange(
        start_index=0, end_index=39, support=Decimal(100), resistance=Decimal(110),
        support_touches=3, resistance_touches=3, inside_ratio=0.95, width_atr=2.5,
    )
    bars = [bar(i, "104", "106", "102", "105") for i in range(40)]
    bars.append(bar(40, "110", "116", "109", "115", "500"))   # пробой
    bars.append(bar(41, "115", "116", "110", "111"))          # ретест удержан
    wyckoff = reading("wyckoff", "wyckoff_phase", {"range": trading_range, "events": []})
    base = dict(
        instrument_id="MOEX:FUT:SIU6",
        context_bars=bars, setup_bars=bars, trigger_bars=bars,
        context_tf="1d", setup_tf="4h", trigger_tf="1h",
        regime=regime(), horizon_days=5, tick_size=Decimal(1),
        wyckoff=wyckoff,
        volume_reading=reading("volume", "volume_confirmation", {}),
        atr_setup=Decimal(4), atr_trigger=Decimal(2),
    )
    base.update(over)
    return SetupContext(**base)


def test_breakout_produces_candidate():
    result = breakout_retest.build(breakout_context())
    assert isinstance(result, Candidate)
    assert result.direction is Direction.LONG
    assert result.rr(1) >= Decimal(2)


def test_breakout_needs_a_range_from_wyckoff_detector():
    """Диапазон один на весь движок: своего определения здесь нет."""
    result = breakout_retest.build(breakout_context(wyckoff=None))
    assert isinstance(result, Rejection)
    assert "range" in {c.name for c in result.failed}


def test_breakout_needs_body():
    """§11.2: тело пробоя ≥0.7 ATR — иначе это не импульс."""
    ctx = breakout_context()
    weak = list(ctx.setup_bars)
    weak[40] = bar(40, "110", "116", "109", "111", "500")   # тело 1 при ATR 4
    result = breakout_retest.build(replace(ctx, setup_bars=weak, context_bars=weak))
    assert isinstance(result, Rejection)
    assert "breakout_body" in {c.name for c in result.failed}


def test_breakout_needs_volume():
    result = breakout_retest.build(breakout_context(volume_reading=None))
    assert isinstance(result, Rejection)
    assert "breakout_volume" in {c.name for c in result.failed}


def test_false_breakout_is_refused_not_reversed():
    """§11.4: ложный пробой отменяет идею, автопереворот запрещён."""
    ctx = breakout_context()
    bars = list(ctx.setup_bars)
    bars[41] = bar(41, "115", "116", "104", "105")   # закрытие глубоко внутрь
    result = breakout_retest.build(replace(ctx, setup_bars=bars, context_bars=bars))
    assert isinstance(result, Rejection)
    assert "retest_held" in {c.name for c in result.failed}
    assert "ложный пробой" in result.reason


def test_breakout_target_is_measured_move():
    result = breakout_retest.build(breakout_context())
    assert isinstance(result, Candidate)
    assert "measured move" in result.targets[1].rationale


# ─── S3 WYCKOFF_REVERSAL §12 ──────────────────────────────────────────────


def reversal_context(**over) -> SetupContext:
    trading_range = TradingRange(
        start_index=25, end_index=60, support=Decimal(100), resistance=Decimal(120),
        support_touches=3, resistance_touches=3, inside_ratio=0.95, width_atr=5.0,
    )
    events = [
        WyckoffEvent("climax", 30, Decimal(101), "объём 3× медианы", 3.0),
        WyckoffEvent("spring", 40, Decimal(98), "прокол на 0.4 ATR", 2.0),
        WyckoffEvent("lps", 46, Decimal(102), "тест удержан"),
    ]
    bars = [bar(i, "140", "142", "138", "139") for i in range(25)]
    bars += [bar(i, "105", "108", "102", "106") for i in range(25, 62)]
    wyckoff = reading(
        "wyckoff", "wyckoff_phase", {"range": trading_range, "events": events}
    )
    smc = reading(
        "smc", "smc_context",
        {"events": [StructureEvent("CHoCH", "up", Decimal(110), 44, 1.2, 40)],
         "swings": [], "order_blocks": [], "fvg": [], "pools": [], "sweeps": []},
    )
    base = dict(
        instrument_id="MOEX:FUT:SIU6",
        context_bars=bars, setup_bars=bars, trigger_bars=bars,
        context_tf="1w", setup_tf="1d", trigger_tf="4h",
        regime=regime(trend=TrendRegime.DOWNTREND, score=-4, structure="down"),
        horizon_days=10, tick_size=Decimal(1),
        wyckoff=wyckoff, smc=smc,
        volume_reading=reading("volume", "volume_confirmation", {}),
        atr_setup=Decimal(4), atr_trigger=Decimal(2),
    )
    base.update(over)
    return SetupContext(**base)


def test_reversal_produces_candidate_when_all_six_hold():
    result = wyckoff_reversal.build(reversal_context())
    assert isinstance(result, Candidate)
    assert result.direction is Direction.LONG
    assert result.risk_multiplier == Decimal("0.75")


@pytest.mark.parametrize(
    ("drop", "expected_check"),
    [("climax", "climax"), ("spring", "spring"), ("lps", "test")],
)
def test_reversal_requires_every_event(drop, expected_check):
    """§12.3: без любого подтверждения — только наблюдение."""
    ctx = reversal_context()
    events = [e for e in ctx.wyckoff.payload["events"] if e.kind != drop]
    trimmed = reading(
        "wyckoff", "wyckoff_phase",
        {**ctx.wyckoff.payload, "events": events},
    )
    result = wyckoff_reversal.build(replace(ctx, wyckoff=trimmed))
    assert isinstance(result, Rejection)
    assert expected_check in {c.name for c in result.failed}


def test_reversal_requires_choch_after_spring_not_before():
    """Порядок проверяется по времени, а не по факту наличия CHoCH."""
    ctx = reversal_context()
    early = reading(
        "smc", "smc_context",
        {**ctx.smc.payload,
         "events": [StructureEvent("CHoCH", "up", Decimal(110), 35, 1.2, 30)]},
    )
    result = wyckoff_reversal.build(replace(ctx, smc=early))
    assert isinstance(result, Rejection)
    assert "choch" in {c.name for c in result.failed}


def test_reversal_requires_prior_move():
    """§12.1: разворачивать нечего, если до диапазона не было движения."""
    ctx = reversal_context()
    flat = [bar(i, "105", "107", "103", "106") for i in range(62)]
    result = wyckoff_reversal.build(replace(ctx, setup_bars=flat, context_bars=flat))
    assert isinstance(result, Rejection)
    assert "prior_move" in {c.name for c in result.failed}


def test_reversal_stop_is_behind_spring():
    """Пробили Spring — разворота не было; другой стоп бессмыслен."""
    result = wyckoff_reversal.build(reversal_context())
    assert isinstance(result, Candidate)
    assert result.stop < Decimal(98)


def test_reversal_rejection_names_every_failed_condition():
    ctx = reversal_context()
    empty = reading("wyckoff", "wyckoff_phase", {**ctx.wyckoff.payload, "events": []})
    result = wyckoff_reversal.build(replace(ctx, wyckoff=empty))
    assert isinstance(result, Rejection)
    assert result.reason
    assert len(result.failed) >= 1


def _on_grid(price: Decimal, tick: Decimal) -> bool:
    return (price / tick) % 1 == 0


@pytest.mark.parametrize(
    ("build", "context"),
    [
        (trend_pullback.build, pullback_context),
        (breakout_retest.build, breakout_context),
        (wyckoff_reversal.build, reversal_context),
    ],
)
def test_все_цены_плана_лежат_на_шаге_инструмента(build, context):
    """Ни одна цена плана не может быть вне сетки цен инструмента.

    Стоп и цели округлялись всегда, зона входа — нет: она приходила из ATR
    числом вроде 1874.023606. Такую заявку биржа не примет, а приложение,
    подбирающее число знаков по самим ценам, из-за одного такого края
    показывало ВСЕ цены идеи с шестью знаками.

    Тест общий для всех стратегий намеренно: следующая забудет округлить
    ровно так же, и поймать это должен не разбор скриншота.
    """
    tick = Decimal("0.01")
    result = build(context(tick_size=tick))
    assert isinstance(result, Candidate), result
    for name, price in (
        ("entry_low", result.entry_low),
        ("entry_high", result.entry_high),
        ("entry_reference", result.entry_reference),
        ("stop", result.stop),
        *[(f"tp{i}", t.price) for i, t in enumerate(result.targets, start=1)],
    ):
        assert _on_grid(price, tick), f"{name}={price} не лежит на шаге {tick}"
    # Опорная цена обязана остаться внутри зоны: из неё считается риск.
    assert result.entry_low <= result.entry_reference <= result.entry_high
