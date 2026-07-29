"""Price Action §10.4: паттерн валиден только в контекстной зоне."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.detectors.price_action import PriceZone, detect
from app.market.candles import Candle

START = datetime(2026, 7, 1, tzinfo=UTC)


def c(i: int, o: str, h: str, low: str, cl: str, vol: str = "100") -> Candle:
    return Candle(
        open_time=START + timedelta(hours=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(low), close=Decimal(cl),
        volume_units=Decimal(vol), source="t",
    )


def background(n: int = 30) -> list[Candle]:
    return [c(i, "100", "102", "98", "100") for i in range(n)]


ZONE = PriceZone(low=Decimal(94), high=Decimal(100), kind="order_block")


# ─── Главное правило ──────────────────────────────────────────────────────


def test_detector_cannot_be_called_without_zones():
    """§10.4 выражено типом: без зон детектор не работает вовсе.

    Не «проверка внутри», а невозможность вызвать в вакууме — правило ТЗ
    перестаёт зависеть от дисциплины вызывающего.
    """
    bars = background() + [c(30, "99", "100", "94", "99.5", "400")]
    assert detect(bars, timeframe="1h", zones=[]) is None


def test_pattern_outside_zone_fails_location_check():
    """Тот же бар вне зоны обязан провалить проверку положения."""
    bars = background() + [c(30, "99", "100", "94", "99.5", "400")]
    far = PriceZone(low=Decimal(50), high=Decimal(60), kind="fvg")
    reading = detect(bars, timeframe="1h", zones=[far])
    if reading is not None:
        location = next(t for t in reading.confidence.terms if t.name == "location")
        assert location.value == 0.0
        assert "вне" in location.note


# ─── Пин-бар ──────────────────────────────────────────────────────────────


def test_pin_bar_in_zone_is_found():
    """Длинный нижний хвост, закрытие в верхней трети, хвост снял уровень."""
    bars = background() + [c(30, "99", "100", "94", "99.5", "400")]
    reading = detect(bars, timeframe="1h", zones=[ZONE])
    assert reading is not None
    assert "Пин-бар" in reading.summary
    assert reading.payload["direction"] == "long"
    assert reading.confidence.value > 0.5


def test_pin_bar_needs_long_wick():
    """Короткий хвост — обычная свеча, а не отбой."""
    bars = background() + [c(30, "99", "100", "98.5", "99.5", "400")]
    reading = detect(bars, timeframe="1h", zones=[ZONE])
    assert reading is None or "Пин-бар" not in reading.summary


def test_pin_bar_needs_close_near_extreme():
    """Хвост длинный, но закрытие в середине — рынок не отбил уровень."""
    bars = background() + [c(30, "97", "100", "94", "96.5", "400")]
    reading = detect(bars, timeframe="1h", zones=[ZONE])
    assert reading is None or "Пин-бар" not in reading.summary


def test_short_pin_bar_is_mirrored():
    zone = PriceZone(low=Decimal(100), high=Decimal(106), kind="fvg")
    bars = background() + [c(30, "101", "106", "100", "100.5", "400")]
    reading = detect(bars, timeframe="1h", zones=[zone])
    assert reading is not None
    assert reading.payload["direction"] == "short"


# ─── Поглощение ───────────────────────────────────────────────────────────


def test_engulfing_checks_all_four_criteria():
    """§10.4 перечисляет четыре признака — все четыре обязаны быть видны."""
    bars = background(29)
    bars.append(c(29, "100", "100.5", "98", "98.5", "100"))   # медвежья
    bars.append(c(30, "98.5", "101", "98", "100.8", "500"))   # бычье поглощение
    reading = detect(
        bars, timeframe="1h", zones=[ZONE], higher_tf_direction="long"
    )
    assert reading is not None
    names = {t.name for t in reading.confidence.terms}
    assert names == {"body_ratio", "location", "relative_volume", "higher_tf"}


def test_engulfing_against_higher_timeframe_loses_confidence():
    """Триггер против старшего таймфрейма — не отказ, но и не подтверждение."""
    bars = background(29)
    bars.append(c(29, "100", "100.5", "98", "98.5", "100"))
    bars.append(c(30, "98.5", "101", "98", "100.8", "500"))
    aligned = detect(bars, timeframe="1h", zones=[ZONE], higher_tf_direction="long")
    against = detect(bars, timeframe="1h", zones=[ZONE], higher_tf_direction="short")
    assert against.confidence.value < aligned.confidence.value


def test_engulfing_without_volume_is_marked_unmeasured():
    """Объём не измерен — так и сказано, а не подставлена единица."""
    bars = [c(i, "100", "102", "98", "100", "100") for i in range(29)]
    bars = [
        Candle(b.open_time, b.open, b.high, b.low, b.close, source="t") for b in bars
    ]
    bars.append(
        Candle(START + timedelta(hours=29), Decimal(100), Decimal("100.5"),
               Decimal(98), Decimal("98.5"), source="t")
    )
    bars.append(
        Candle(START + timedelta(hours=30), Decimal("98.5"), Decimal(101),
               Decimal(98), Decimal("100.8"), source="t")
    )
    reading = detect(bars, timeframe="1h", zones=[ZONE])
    if reading is not None:
        measure = reading.measure("trigger_relative_volume")
        assert measure.status == "missing"
        assert measure.note


# ─── Ложный пробой ────────────────────────────────────────────────────────


def test_failed_breakout_is_found():
    """Вышли за зону и вернулись закрытием — вход в обратную сторону."""
    zone = PriceZone(low=Decimal(98), high=Decimal(102), kind="range_boundary")
    bars = background(29)
    bars.append(c(29, "102", "105", "102", "104", "300"))    # пробой вверх
    bars.append(c(30, "104", "104.5", "100", "100.5", "400"))  # вернулись внутрь
    reading = detect(bars, timeframe="1h", zones=[zone])
    assert reading is not None
    assert reading.payload["direction"] == "short"


# ─── Разметка ─────────────────────────────────────────────────────────────


def test_mark_highlights_the_actual_candle():
    """§10.4: выделяется конкретная свеча или группа, а не абстрактная зона."""
    bars = background() + [c(30, "99", "100", "94", "99.5", "400")]
    reading = detect(bars, timeframe="1h", zones=[ZONE])
    mark = reading.marks[0]
    assert mark.price_low == Decimal(94) and mark.price_high == Decimal(100)
    assert mark.end_time == bars[-1].open_time
    assert mark.kind == "pa_pin_bar"
    assert mark.timeframe == "1h"


def test_version_travels_with_reading():
    bars = background() + [c(30, "99", "100", "94", "99.5", "400")]
    reading = detect(bars, timeframe="1h", zones=[ZONE])
    assert reading.version == "price-action-1.0.0"
    assert reading.evidence_kind == "entry_trigger"


def test_quiet_market_produces_nothing():
    """Нет триггера — нет наблюдения. Пустое читалось бы как «проверено и слабо»."""
    assert detect(background(40), timeframe="1h", zones=[ZONE]) is None
