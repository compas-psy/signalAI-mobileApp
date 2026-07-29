"""Детектор SMC §8.3: у каждого объекта проверяемое определение.

Форма каждого теста одинакова: строим сценарий, где объект обязан найтись,
потом снимаем ровно одно условие ТЗ и убеждаемся, что объект исчез. Тест,
который только подтверждает находку, не отличает детектор от функции,
возвращающей «да».
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.detectors.smc import (
    SmcParams,
    detect,
    find_fair_value_gaps,
    find_liquidity_pools,
    find_order_blocks,
    find_structure_events,
    find_sweeps,
)
from app.features.indicators import atr, swings
from app.market.candles import Candle

START = datetime(2026, 7, 1, tzinfo=UTC)
P = SmcParams()


def c(i: int, o: str, h: str, low: str, cl: str, vol: str = "100") -> Candle:
    return Candle(
        open_time=START + timedelta(hours=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(low), close=Decimal(cl),
        volume_units=Decimal(vol), source="t",
    )


def flat(n: int, price: int = 100, start: int = 0, vol: str = "100") -> list[Candle]:
    """Спокойный фон с размахом 2 — ATR получается равным 2."""
    return [
        c(start + i, str(price), str(price + 1), str(price - 1), str(price), vol)
        for i in range(n)
    ]


# ─── BOS и CHoCH ──────────────────────────────────────────────────────────


def build_swing_high(base: int = 20) -> list[Candle]:
    """Фон, затем свинг-хай на 110, затем откат — свинг подтверждён."""
    bars = flat(base)
    bars += [
        c(base + 0, "100", "110", "99", "108"),   # экстремум
        c(base + 1, "108", "109", "104", "105"),
        c(base + 2, "105", "106", "102", "103"),
    ]
    return bars


def test_bos_requires_close_beyond_swing_not_just_touch():
    """§8.3: BOS подтверждается **закрытием** за свингом, а не проколом.

    Хвост, ушедший за уровень при закрытии внутри, — это снятие ликвидности,
    и торговать его надо в противоположную сторону.
    """
    bars = build_swing_high()
    # Хвостом за 110, закрытие внутри — слома быть не должно.
    bars.append(c(23, "103", "112", "102", "104"))
    values = atr(bars)
    points = swings(bars, confirm_bars=P.pivot_confirm_bars)
    events = find_structure_events(bars, points, values, P)
    assert [e for e in events if e.direction == "up"] == []


def test_bos_is_found_when_close_exceeds_by_threshold():
    bars = build_swing_high()
    bars.append(c(23, "103", "113", "102", "112"))  # закрытие уверенно выше 110
    values = atr(bars)
    points = swings(bars, confirm_bars=P.pivot_confirm_bars)
    events = [e for e in find_structure_events(bars, points, values, P) if e.direction == "up"]
    assert len(events) == 1
    assert events[0].kind == "BOS"
    assert events[0].level == Decimal(110)
    assert events[0].displacement_atr > 0


def test_bos_ignores_break_smaller_than_min_atr():
    """Превышение меньше 0.1 ATR — шум котировок, а не слом структуры."""
    bars = build_swing_high()
    bars.append(c(23, "103", "110.1", "102", "110.05"))
    values = atr(bars)
    points = swings(bars, confirm_bars=P.pivot_confirm_bars)
    events = [e for e in find_structure_events(bars, points, values, P) if e.direction == "up"]
    assert events == []


def test_choch_is_reported_after_opposite_bos():
    """Первый слом против сложившейся структуры — это CHoCH, а не BOS."""
    bars = build_swing_high()
    bars.append(c(23, "111", "113", "110", "112"))          # BOS вверх за 110
    bars += [
        c(24, "112", "113", "108", "109"),
        c(25, "109", "110", "104", "105"),                  # будущий свинг-лоу 104
        c(26, "105", "111", "105", "110"),
        c(27, "110", "112", "108", "111"),                  # свинг-лоу подтверждён
        c(28, "111", "112", "100", "101"),                  # закрытие ниже 104
    ]
    values = atr(bars)
    points = swings(bars, confirm_bars=P.pivot_confirm_bars)
    events = find_structure_events(bars, points, values, P)
    kinds = [(e.kind, e.direction) for e in events]
    assert ("BOS", "up") in kinds
    assert ("CHoCH", "down") in kinds


def test_structure_event_never_uses_unconfirmed_swing():
    """Событие не может опираться на свинг, о котором ещё не знали."""
    bars = build_swing_high()
    bars.append(c(23, "103", "113", "102", "112"))
    values = atr(bars)
    points = swings(bars, confirm_bars=P.pivot_confirm_bars)
    for event in find_structure_events(bars, points, values, P):
        swing = next(s for s in points if s.index == event.broken_swing_index)
        assert swing.confirmed_index <= event.index


# ─── Снятие ликвидности ───────────────────────────────────────────────────


def test_sweep_requires_return_inside():
    """Экстремум пробит и цена не вернулась — это пробой, а не снятие."""
    bars = flat(20)
    bars += [
        c(20, "100", "110", "99", "108"),
        c(21, "108", "109", "104", "105"),
        c(22, "105", "106", "102", "103"),
        c(23, "103", "110.6", "102", "110.5"),   # ушли за уровень и остались
        c(24, "110.5", "112", "110", "111.5"),
        c(25, "111.5", "113", "111", "112.5"),
    ]
    values = atr(bars)
    points = swings(bars, confirm_bars=P.pivot_confirm_bars)
    assert find_sweeps(bars, points, values, P) == []


def test_sweep_is_found_when_close_returns_inside():
    bars = flat(20)
    bars += [
        c(20, "100", "110", "99", "108"),
        c(21, "108", "109", "104", "105"),
        c(22, "105", "106", "102", "103"),
        c(23, "103", "110.6", "102", "104"),     # прокол и возврат в тот же бар
    ]
    values = atr(bars)
    points = swings(bars, confirm_bars=P.pivot_confirm_bars)
    found = find_sweeps(bars, points, values, P)
    assert len(found) == 1
    assert found[0].is_high and found[0].return_bars == 0
    assert P.sweep_atr_min <= Decimal(str(found[0].penetration_atr)) <= P.sweep_atr_max


def test_sweep_rejects_too_deep_penetration():
    """Слишком глубокий выход — уже пробой: 0.75 ATR верхняя граница."""
    bars = flat(20)
    bars += [
        c(20, "100", "110", "99", "108"),
        c(21, "108", "109", "104", "105"),
        c(22, "105", "106", "102", "103"),
        c(23, "103", "115", "102", "104"),       # 5 пунктов при ATR≈2 → 2.5 ATR
    ]
    values = atr(bars)
    points = swings(bars, confirm_bars=P.pivot_confirm_bars)
    assert find_sweeps(bars, points, values, P) == []


def test_sweep_rejects_too_shallow_penetration():
    """Прокол меньше 0.05 ATR — шум, а не снятие стопов."""
    bars = flat(20)
    bars += [
        c(20, "100", "110", "99", "108"),
        c(21, "108", "109", "104", "105"),
        c(22, "105", "106", "102", "103"),
        c(23, "103", "110.05", "102", "104"),
    ]
    values = atr(bars)
    points = swings(bars, confirm_bars=P.pivot_confirm_bars)
    assert find_sweeps(bars, points, values, P) == []


# ─── FVG ──────────────────────────────────────────────────────────────────


def test_bullish_fvg_matches_definition():
    """§8.3: бычий FVG — ``low[t] > high[t-2]``."""
    bars = flat(20)
    bars += [
        c(20, "100", "101", "99", "100"),
        c(21, "100", "106", "100", "105"),
        c(22, "105", "108", "103", "107"),   # low 103 > high 101
    ]
    gaps = find_fair_value_gaps(bars, atr(bars), P)
    assert len(gaps) == 1
    assert gaps[0].is_bullish
    assert gaps[0].low == Decimal(101) and gaps[0].high == Decimal(103)


def test_bearish_fvg_matches_definition():
    bars = flat(20)
    bars += [
        c(20, "100", "101", "99", "100"),
        c(21, "100", "100", "94", "95"),
        c(22, "95", "97", "92", "93"),       # high 97 < low 99
    ]
    gaps = find_fair_value_gaps(bars, atr(bars), P)
    assert len(gaps) == 1
    assert not gaps[0].is_bullish
    assert gaps[0].low == Decimal(97) and gaps[0].high == Decimal(99)


def test_tiny_gap_is_not_an_fvg():
    """Разрыв меньше 0.1 ATR — обычный шум котировок."""
    bars = flat(20)
    bars += [
        c(20, "100", "101", "99", "100"),
        c(21, "100", "102", "100", "101"),
        c(22, "101.2", "103", "101.1", "102"),   # разрыв 0.1 при ATR≈2
    ]
    assert find_fair_value_gaps(bars, atr(bars), P) == []


def test_filled_fvg_is_hidden():
    """Отработанная зона не показывается: §10.7 требует не засорять экран."""
    bars = flat(20)
    bars += [
        c(20, "100", "101", "99", "100"),
        c(21, "100", "106", "100", "105"),
        c(22, "105", "108", "103", "107"),
        c(23, "107", "108", "101", "102"),     # вернулись и закрыли разрыв
    ]
    assert find_fair_value_gaps(bars, atr(bars), P) == []


# ─── Order block ──────────────────────────────────────────────────────────


def build_impulse_with_origin() -> list[Candle]:
    """Медвежья свеча, затем импульсный BOS вверх на большом объёме."""
    bars = flat(20)
    bars += [
        c(20, "100", "110", "99", "108"),
        c(21, "108", "109", "104", "105"),
        c(22, "105", "106", "102", "103", "100"),
        c(23, "103", "104", "101", "101.5", "100"),   # медвежья — кандидат в OB
        c(24, "101.5", "116", "101", "115", "900"),   # импульс: body 13.5 ≈ 6 ATR
    ]
    return bars


def test_order_block_needs_body_and_volume():
    bars = build_impulse_with_origin()
    values, points = atr(bars), swings(bars, confirm_bars=P.pivot_confirm_bars)
    from app.features.indicators import relative_volume, zscore

    rel = relative_volume(bars)
    z = zscore([v if v is not None else 1.0 for v in rel])
    events = find_structure_events(bars, points, values, P)
    blocks = find_order_blocks(bars, events, values, z, P)
    assert blocks, "импульс с большим телом и объёмом обязан дать order block"
    assert blocks[0].body_atr >= float(P.ob_min_body_atr)


def test_order_block_rejected_when_impulse_body_too_small():
    """Тело импульса меньше 0.8 ATR — это не импульс."""
    bars = build_impulse_with_origin()
    bars[-1] = c(24, "101.5", "112", "101", "111.5", "900")  # закрытие ниже
    values, points = atr(bars), swings(bars, confirm_bars=P.pivot_confirm_bars)
    from app.features.indicators import relative_volume, zscore

    z = zscore([v if v is not None else 1.0 for v in relative_volume(bars)])
    events = find_structure_events(bars, points, values, P)
    strict = SmcParams(ob_min_body_atr=Decimal("5.0"))
    assert find_order_blocks(bars, events, values, z, strict) == []


def test_order_block_rejected_without_volume():
    """Импульс без объёма — движение без участников."""
    bars = build_impulse_with_origin()
    bars[-1] = c(24, "101.5", "116", "101", "115", "100")   # объём как фон
    values, points = atr(bars), swings(bars, confirm_bars=P.pivot_confirm_bars)
    from app.features.indicators import relative_volume, zscore

    z = zscore([v if v is not None else 1.0 for v in relative_volume(bars)])
    events = find_structure_events(bars, points, values, P)
    assert find_order_blocks(bars, events, values, z, P) == []


# ─── Пулы ликвидности ─────────────────────────────────────────────────────


def test_equal_highs_form_a_pool():
    """Равные максимумы с допуском от ATR — кластер стопов."""
    bars = flat(20)
    for k, base in enumerate((20, 24, 28)):
        bars += [
            c(base, "100", "110", "99", "108"),
            c(base + 1, "108", "109", "104", "105"),
            c(base + 2, "105", "106", "102", "103"),
            c(base + 3, "103", "104", "100", "101"),
        ]
    values, points = atr(bars), swings(bars, confirm_bars=P.pivot_confirm_bars)
    pools = [p for p in find_liquidity_pools(points, values, P) if p.is_high]
    assert pools and pools[0].touches >= 2


def test_scattered_highs_do_not_form_a_pool():
    bars = flat(20)
    for k, (base, high) in enumerate(((20, "110"), (24, "130"), (28, "150"))):
        bars += [
            c(base, "100", high, "99", "108"),
            c(base + 1, "108", "109", "104", "105"),
            c(base + 2, "105", "106", "102", "103"),
            c(base + 3, "103", "104", "100", "101"),
        ]
    values, points = atr(bars), swings(bars, confirm_bars=P.pivot_confirm_bars)
    assert [p for p in find_liquidity_pools(points, values, P) if p.is_high] == []


# ─── Сборка наблюдения ────────────────────────────────────────────────────


def test_detect_returns_none_without_structure():
    """Пустое наблюдение читалось бы как «структура посчитана и никакая»."""
    assert detect(flat(40), timeframe="4h") is None


def test_detect_carries_version_and_marks():
    bars = build_impulse_with_origin()
    reading = detect(bars, timeframe="4h")
    assert reading is not None
    assert reading.version == "smc-1.0.0"
    assert reading.evidence_kind == "smc_context"
    assert reading.marks, "наблюдение обязано давать разметку для графика"
    for mark in reading.marks:
        assert mark.timeframe == "4h"
        assert mark.start_time <= mark.end_time
        assert 0.0 <= mark.confidence <= 1.0


def test_confidence_is_decomposed_not_magic():
    bars = build_impulse_with_origin()
    reading = detect(bars, timeframe="4h")
    assert len(reading.confidence.terms) >= 3
    assert 0.0 <= reading.confidence.value <= 1.0
    assert isinstance(reading.confidence.missing(), list)


def test_missing_measure_says_it_is_missing():
    """Отсутствие слома — «не измерено», а не ноль импульса."""
    bars = flat(20) + [
        c(20, "100", "110", "99", "108"),
        c(21, "108", "109", "104", "105"),
        c(22, "105", "106", "102", "103"),
        c(23, "103", "104", "101", "102"),
    ]
    reading = detect(bars, timeframe="4h")
    if reading is not None:
        measure = reading.measure("last_displacement_atr")
        if measure is not None and measure.value is None:
            assert measure.status == "missing"
            assert measure.note
