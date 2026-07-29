"""Вайкофф §8.4, §11.1, §12: диапазон и события с проверяемыми определениями."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.detectors.wyckoff import WyckoffParams, detect, find_events, find_range
from app.features.indicators import atr, relative_volume, swings
from app.market.candles import Candle

START = datetime(2026, 7, 1, tzinfo=UTC)
P = WyckoffParams()


def c(i: int, o: str, h: str, low: str, cl: str, vol: str = "100") -> Candle:
    return Candle(
        open_time=START + timedelta(hours=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(low), close=Decimal(cl),
        volume_units=Decimal(vol), source="t",
    )


def range_market(bars: int = 40, support: int = 100, resistance: int = 110):
    """Колебание между границами с многократными касаниями обеих.

    Бары намеренно узкие относительно диапазона: ширина в ATR должна попадать
    в коридор §11.1 (2–8 ATR). Широкие бары дали бы ATR соизмеримый с самим
    диапазоном, и диапазона бы не было — что для реального рынка тоже верно.
    """
    wave = [101, 103, 105, 107, 109, 109, 107, 105, 103, 101]
    out = []
    prev = wave[0]
    for i in range(bars):
        close = wave[i % len(wave)]
        high = min(resistance, max(close, prev) + 1)
        low = max(support, min(close, prev) - 1)
        out.append(c(i, str(prev), str(high), str(low), str(close)))
        prev = close
    return out


def parts(candles):
    return atr(candles), swings(candles, confirm_bars=2), relative_volume(candles)


# ─── Диапазон §11.1 ───────────────────────────────────────────────────────


def test_range_is_found_when_all_three_conditions_hold():
    bars = range_market()
    values, points, _ = parts(bars)
    found = find_range(bars, values, points, P)
    assert found is not None
    assert found.support_touches >= P.min_boundary_touches
    assert found.resistance_touches >= P.min_boundary_touches
    assert P.min_width_atr <= Decimal(str(found.width_atr)) <= P.max_width_atr


def test_range_needs_minimum_duration():
    """Короткий участок — не диапазон, иначе им окажется любой кусок графика."""
    bars = range_market(bars=12)
    values, points, _ = parts(bars)
    assert find_range(bars, values, points, P) is None


def test_range_needs_two_touches_of_each_boundary():
    """Одно касание границы не делает её уровнем."""
    bars = range_market()
    strict = WyckoffParams(min_boundary_touches=15)
    values, points, _ = parts(bars)
    assert find_range(bars, values, points, strict) is None


def test_range_rejects_too_wide_and_too_narrow():
    bars = range_market()
    values, points, _ = parts(bars)
    too_narrow = WyckoffParams(min_width_atr=Decimal("50"))
    too_wide = WyckoffParams(max_width_atr=Decimal("0.1"))
    assert find_range(bars, values, points, too_narrow) is None
    assert find_range(bars, values, points, too_wide) is None


def test_boundary_is_a_cluster_not_a_single_spike():
    """Один выброс не задирает границу.

    Иначе пробой такой границы не случится никогда, а ретест окажется в
    пустоте.
    """
    bars = range_market()
    bars[10] = c(10, "105", "140", "104", "106")   # одиночный шип
    values, points, _ = parts(bars)
    found = find_range(bars, values, points, P)
    assert found is not None
    assert found.resistance < Decimal(130)


def test_trending_market_is_not_a_range():
    bars = [c(i, str(100 + i), str(102 + i), str(98 + i), str(101 + i)) for i in range(40)]
    values, points, _ = parts(bars)
    assert find_range(bars, values, points, P) is None


# ─── События §8.4, §12.2 ──────────────────────────────────────────────────


def with_spring(bars):
    """Прокол поддержки на ~0.4 ATR с возвратом внутрь и подтверждением."""
    n = len(bars)
    bars = list(bars)
    bars.append(c(n, "102", "103", "98.5", "102"))     # spring
    bars.append(c(n + 1, "102", "106", "101", "105"))  # следующий бар не подтвердил слом
    return bars


def test_spring_requires_close_back_inside():
    bars = with_spring(range_market())
    values, points, rel = parts(bars)
    trading_range = find_range(bars, values, points, P)
    events = find_events(bars, trading_range, values, rel, P)
    assert any(e.kind == "spring" for e in events)


def test_spring_rejected_when_price_stays_below():
    """Цена ушла и осталась — это пробой вниз, а не Spring."""
    bars = list(range_market())
    n = len(bars)
    bars.append(c(n, "102", "103", "98.5", "99"))
    bars.append(c(n + 1, "99", "99.5", "96", "97"))
    values, points, rel = parts(bars)
    trading_range = find_range(bars, values, points, P)
    if trading_range is not None:
        events = find_events(bars, trading_range, values, rel, P)
        assert not any(e.kind == "spring" for e in events)


def test_climax_needs_abnormal_volume():
    bars = list(range_market())
    n = len(bars)
    bars.append(c(n, "102", "112", "97", "104", "1000"))
    values, points, rel = parts(bars)
    trading_range = find_range(bars, values, points, P)
    events = find_events(bars, trading_range, values, rel, P)
    assert any(e.kind == "climax" for e in events)


def test_no_climax_without_volume():
    bars = list(range_market())
    n = len(bars)
    bars.append(c(n, "102", "112", "97", "104", "100"))
    values, points, rel = parts(bars)
    trading_range = find_range(bars, values, points, P)
    events = find_events(bars, trading_range, values, rel, P)
    assert not any(e.kind == "climax" for e in events)


def test_lps_never_appears_without_sos():
    """§8.4: LPS появляется только после SOS и успешного теста."""
    bars = with_spring(range_market())
    values, points, rel = parts(bars)
    trading_range = find_range(bars, values, points, P)
    events = find_events(bars, trading_range, values, rel, P)
    kinds = {e.kind for e in events}
    if "sos" not in kinds:
        assert "lps" not in kinds


# ─── Сборка наблюдения ────────────────────────────────────────────────────


def test_low_confidence_reading_is_not_emitted():
    """§10.2 и §9.1 вместе означают: слабое наблюдение не выпускается вовсе.

    Нельзя показать слой, не участвовавший в оценке, и нельзя показать слой
    с уверенностью ниже порога. Единственный способ выполнить оба требования.
    """
    bars = range_market()
    assert detect(bars, timeframe="4h", params=WyckoffParams(min_confidence=0.99)) is None


def test_reading_carries_version_and_range_mark():
    bars = with_spring(range_market())
    reading = detect(bars, timeframe="4h", params=WyckoffParams(min_confidence=0.0))
    assert reading is not None
    assert reading.version == "wyckoff-1.0.0"
    assert reading.evidence_kind == "wyckoff_phase"
    range_marks = [m for m in reading.marks if m.kind == "wyckoff_range"]
    assert len(range_marks) == 1
    assert range_marks[0].price_low < range_marks[0].price_high


def test_confidence_names_what_is_missing():
    """Разбор идеи должен показывать, какого подтверждения не хватило."""
    bars = range_market()
    reading = detect(bars, timeframe="4h", params=WyckoffParams(min_confidence=0.0))
    assert reading is not None
    missing = reading.confidence.missing()
    assert "sweep" in missing or "test" in missing or "volume" in missing


def test_no_range_no_reading():
    trend = [c(i, str(100 + i), str(102 + i), str(98 + i), str(101 + i)) for i in range(50)]
    assert detect(trend, timeframe="4h") is None
