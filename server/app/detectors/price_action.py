"""Свечные триггеры (engine-ТЗ §8, §10.3; UX-ТЗ §10.4).

Главное правило вынесено в подпись функции: **паттерн валиден только в
контекстной зоне**. ``detect`` требует список зон и без них возвращает
``None``. Это не проверка внутри — это невозможность вызвать детектор «в
вакууме»: правило ТЗ выражено типом, а не дисциплиной вызывающего.

Для каждого паттерна проверяются ровно те признаки, что перечислены в ТЗ:
для поглощения — соотношение тел, положение относительно зоны,
относительный объём и направление старшего таймфрейма; для пин-бара и
отбоя — длина хвоста, место закрытия и снятие уровня.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..features.indicators import atr, relative_volume
from ..market.candles import Candle
from .base import Confidence, ConfidenceTerm, Mark, Measure, Reading

VERSION = "price-action-1.0.0"
EVIDENCE_KIND = "entry_trigger"


@dataclass(frozen=True, slots=True)
class PriceZone:
    """Контекстная зона, внутри которой паттерн вообще имеет смысл."""

    low: Decimal
    high: Decimal
    kind: str  # 'order_block' | 'fvg' | 'fib' | 'range_boundary' | 'ema'

    def contains(self, price: Decimal) -> bool:
        return self.low <= price <= self.high

    def overlaps(self, low: Decimal, high: Decimal) -> bool:
        return not (high < self.low or low > self.high)


@dataclass(frozen=True, slots=True)
class PaParams:
    engulfing_body_ratio: float = 1.1
    reversal_body_atr: Decimal = Decimal("0.60")
    min_relative_volume: float = 1.2
    pin_wick_ratio: float = 0.60
    pin_close_location: float = 0.33
    failed_breakout_bars: int = 2


@dataclass(frozen=True, slots=True)
class Trigger:
    kind: str
    index: int
    direction: str  # 'long' | 'short'
    zone_kind: str
    body_atr: float
    relative_volume: float | None
    detail: str
    checks: tuple[tuple[str, bool, str], ...]

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.checks)


def _location(candle: Candle) -> float:
    """Где закрытие внутри бара: 0 — на минимуме, 1 — на максимуме."""
    span = candle.high - candle.low
    if span == 0:
        return 0.5
    return float((candle.close - candle.low) / span)


def _find_engulfing(
    candles: Sequence[Candle], i: int, zones: Sequence[PriceZone],
    atr_value: Decimal, rel_vol: float | None, higher_tf: str | None, p: PaParams,
) -> Trigger | None:
    if i == 0:
        return None
    prev, curr = candles[i - 1], candles[i]
    if curr.body == 0 or prev.body == 0:
        return None
    direction = "long" if curr.is_up else "short"
    if curr.is_up == prev.is_up:
        return None

    zone = next(
        (z for z in zones if z.overlaps(curr.low, curr.high)), None
    )
    checks = (
        (
            "body_ratio",
            float(curr.body / prev.body) >= p.engulfing_body_ratio,
            f"тело {float(curr.body / prev.body):.2f} против порога "
            f"{p.engulfing_body_ratio}",
        ),
        (
            "location",
            zone is not None,
            "в контекстной зоне" if zone else "вне контекстной зоны — паттерн не в счёт",
        ),
        (
            "relative_volume",
            rel_vol is not None and rel_vol >= p.min_relative_volume,
            f"объём {rel_vol:.2f}× медианы" if rel_vol is not None else "объём не измерен",
        ),
        (
            "higher_tf",
            higher_tf is None or higher_tf == direction,
            f"старший ТФ: {higher_tf or 'не задан'}",
        ),
    )
    return Trigger(
        kind="engulfing",
        index=i,
        direction=direction,
        zone_kind=zone.kind if zone else "",
        body_atr=float(curr.body / atr_value) if atr_value > 0 else 0.0,
        relative_volume=rel_vol,
        detail="Поглощение" + (f" в зоне {zone.kind}" if zone else ""),
        checks=checks,
    )


def _find_pin(
    candles: Sequence[Candle], i: int, zones: Sequence[PriceZone],
    atr_value: Decimal, rel_vol: float | None, p: PaParams,
) -> Trigger | None:
    candle = candles[i]
    span = candle.range
    if span == 0:
        return None
    lower = float(candle.lower_wick / span)
    upper = float(candle.upper_wick / span)
    location = _location(candle)

    if lower >= p.pin_wick_ratio and location >= 1 - p.pin_close_location:
        direction, wick, swept = "long", lower, candle.low
    elif upper >= p.pin_wick_ratio and location <= p.pin_close_location:
        direction, wick, swept = "short", upper, candle.high
    else:
        return None

    zone = next((z for z in zones if z.overlaps(candle.low, candle.high)), None)
    checks = (
        ("wick_length", wick >= p.pin_wick_ratio, f"хвост {wick:.0%} бара"),
        (
            "close_location",
            True,
            f"закрытие в {'верхней' if direction == 'long' else 'нижней'} трети",
        ),
        (
            "level_sweep",
            zone is not None and zone.contains(swept),
            "хвост снял уровень зоны" if zone and zone.contains(swept)
            else "уровень зоны не снят",
        ),
        ("location", zone is not None, "в контекстной зоне" if zone else "вне зоны"),
    )
    return Trigger(
        kind="pin_bar",
        index=i,
        direction=direction,
        zone_kind=zone.kind if zone else "",
        body_atr=float(candle.body / atr_value) if atr_value > 0 else 0.0,
        relative_volume=rel_vol,
        detail="Пин-бар" + (f" на зоне {zone.kind}" if zone else ""),
        checks=checks,
    )


def _find_failed_breakout(
    candles: Sequence[Candle], i: int, zones: Sequence[PriceZone],
    atr_value: Decimal, rel_vol: float | None, p: PaParams,
) -> Trigger | None:
    """Ложный пробой: вышли за зону и вернулись закрытием в пределах N баров."""
    if i < p.failed_breakout_bars:
        return None
    for zone in zones:
        broke_up = any(
            candles[j].high > zone.high and candles[j].close > zone.high
            for j in range(i - p.failed_breakout_bars, i)
        )
        broke_down = any(
            candles[j].low < zone.low and candles[j].close < zone.low
            for j in range(i - p.failed_breakout_bars, i)
        )
        candle = candles[i]
        if broke_up and candle.close < zone.high:
            direction = "short"
        elif broke_down and candle.close > zone.low:
            direction = "long"
        else:
            continue
        return Trigger(
            kind="failed_breakout",
            index=i,
            direction=direction,
            zone_kind=zone.kind,
            body_atr=float(candle.body / atr_value) if atr_value > 0 else 0.0,
            relative_volume=rel_vol,
            detail=f"Ложный пробой зоны {zone.kind}",
            checks=(
                ("returned", True, "закрытие вернулось за границу зоны"),
                ("location", True, f"зона {zone.kind}"),
            ),
        )
    return None


def detect(
    candles: Sequence[Candle],
    *,
    timeframe: str,
    zones: Sequence[PriceZone],
    higher_tf_direction: str | None = None,
    params: PaParams | None = None,
    atr_period: int = 14,
) -> Reading | None:
    """Найти триггер на последних барах.

    ``zones`` обязательны. Без контекстной зоны паттерн не рассматривается
    вовсе — §10.4: «Паттерн не считается самостоятельным сигналом».
    """
    p = params or PaParams()
    if not zones or len(candles) < atr_period + 2:
        return None

    atr_values = atr(candles, atr_period)
    rel_vols = relative_volume(candles)
    last = len(candles) - 1
    atr_value = atr_values[last] or Decimal(0)
    rel_vol = rel_vols[last]

    candidates = [
        _find_engulfing(candles, last, zones, atr_value, rel_vol, higher_tf_direction, p),
        _find_pin(candles, last, zones, atr_value, rel_vol, p),
        _find_failed_breakout(candles, last, zones, atr_value, rel_vol, p),
    ]
    triggers = [t for t in candidates if t is not None]
    if not triggers:
        return None

    best = max(triggers, key=lambda t: (t.passed, t.body_atr))
    candle = candles[best.index]

    terms = tuple(
        ConfidenceTerm(name, 0.25, 1.0 if ok else 0.0, note)
        for name, ok, note in best.checks
    )

    return Reading(
        detector="price_action",
        version=VERSION,
        evidence_kind=EVIDENCE_KIND,
        summary=best.detail,
        detail="; ".join(f"{note}" for _, _, note in best.checks),
        confidence=Confidence(terms),
        measures=(
            Measure("trigger_body_atr", "Тело триггера, ATR", best.body_atr),
            Measure(
                "trigger_relative_volume",
                "Объём триггера к медиане",
                best.relative_volume,
                status="measured" if best.relative_volume is not None else "missing",
                note="" if best.relative_volume is not None else "объём не измерен",
            ),
        ),
        marks=(
            Mark(
                kind={
                    "engulfing": "pa_engulfing",
                    "pin_bar": "pa_pin_bar",
                    "failed_breakout": "pa_failed_breakout",
                }[best.kind],
                timeframe=timeframe,
                start_time=candles[max(0, best.index - 1)].open_time,
                end_time=candle.open_time,
                price_low=candle.low,
                price_high=candle.high,
                label=best.detail,
                confidence=Confidence(terms).value,
                display_priority=90,
            ),
        ),
        payload={"trigger": best, "direction": best.direction},
    )
