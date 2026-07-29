"""Диапазон и события Вайкоффа (engine-ТЗ §8.4, §11.1, §12).

Диапазон здесь — единственный на весь движок. Стратегия «пробой с ретестом»
берёт его отсюда же: два независимых определения диапазона неизбежно
разъедутся, и тогда одна и та же цена окажется одновременно внутри и снаружи.

Границы строятся не по минимуму и максимуму окна, а по **кластеру касаний**:
один выброс не должен задирать границу — иначе пробой этой границы никогда
не случится, а ретест окажется в пустоте.

Событие без подтверждения не выпускается. §12.3 прямо перечисляет, что
обязательно для сделки: CHoCH, импульс от Spring, LPS и удержание ретеста.
Без них Spring остаётся наблюдением, а не сетапом.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..features.indicators import Swing, atr, relative_volume, swings
from ..market.candles import Candle
from .base import Confidence, ConfidenceTerm, Mark, Measure, Reading

VERSION = "wyckoff-1.0.0"
EVIDENCE_KIND = "wyckoff_phase"


@dataclass(frozen=True, slots=True)
class WyckoffParams:
    """Пороги §11.1 и §12."""

    min_range_bars: int = 20
    min_width_atr: Decimal = Decimal("2.0")
    max_width_atr: Decimal = Decimal("8.0")
    min_boundary_touches: int = 2
    touch_tolerance_atr: Decimal = Decimal("0.25")
    min_inside_ratio: float = 0.85
    spring_atr_min: Decimal = Decimal("0.10")
    spring_atr_max: Decimal = Decimal("0.70")
    sos_body_atr: Decimal = Decimal("0.80")
    sos_relative_volume: float = 1.5
    climax_relative_volume: float = 2.0
    min_prior_move_atr: Decimal = Decimal("3.0")
    min_confidence: float = 0.55


@dataclass(frozen=True, slots=True)
class TradingRange:
    start_index: int
    end_index: int
    support: Decimal
    resistance: Decimal
    support_touches: int
    resistance_touches: int
    inside_ratio: float
    width_atr: float

    @property
    def height(self) -> Decimal:
        return self.resistance - self.support

    @property
    def midpoint(self) -> Decimal:
        return (self.support + self.resistance) / 2

    @property
    def bars(self) -> int:
        return self.end_index - self.start_index + 1


@dataclass(frozen=True, slots=True)
class WyckoffEvent:
    kind: str  # climax | spring | upthrust | sos | sow | lps | lpsy
    index: int
    price: Decimal
    detail: str
    relative_volume: float | None = None


def _cluster_level(prices: Sequence[Decimal], tolerance: Decimal) -> Decimal:
    """Уровень как медиана самого населённого кластера.

    Максимум окна — это один бар; уровень, на котором стоят заявки, — это
    место, куда цена приходила много раз.
    """
    if not prices:
        raise ValueError("нет цен для кластеризации")
    best: list[Decimal] = []
    for anchor in prices:
        members = [p for p in prices if abs(p - anchor) <= tolerance]
        if len(members) > len(best):
            best = members
    ordered = sorted(best)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def find_range(
    candles: Sequence[Candle],
    atr_values: Sequence[Decimal | None],
    swing_points: Sequence[Swing],
    params: WyckoffParams,
) -> TradingRange | None:
    """Диапазон по §11.1: длительность, ширина и число касаний границ.

    Проверяются все три условия. Снятие любого из них обязано убрать
    диапазон — иначе «диапазоном» окажется любой участок графика.
    """
    n = len(candles)
    if n < params.min_range_bars:
        return None

    end = n - 1
    current_atr = atr_values[end]
    if current_atr is None or current_atr <= 0:
        return None
    tolerance = current_atr * params.touch_tolerance_atr

    best: TradingRange | None = None
    for length in range(params.min_range_bars, min(n, 120) + 1):
        start = end - length + 1
        if start < 0:
            break
        window = candles[start : end + 1]

        highs = [c.high for c in window]
        lows = [c.low for c in window]
        resistance = _cluster_level(highs, tolerance)
        support = _cluster_level(lows, tolerance)
        if resistance <= support:
            continue

        width = resistance - support
        width_atr = width / current_atr
        if not (params.min_width_atr <= width_atr <= params.max_width_atr):
            continue

        res_touches = sum(1 for h in highs if abs(h - resistance) <= tolerance)
        sup_touches = sum(1 for low in lows if abs(low - support) <= tolerance)
        if (
            res_touches < params.min_boundary_touches
            or sup_touches < params.min_boundary_touches
        ):
            continue

        inside = sum(1 for c in window if support - tolerance <= c.close <= resistance + tolerance)
        inside_ratio = inside / len(window)
        if inside_ratio < params.min_inside_ratio:
            continue

        candidate = TradingRange(
            start_index=start,
            end_index=end,
            support=support,
            resistance=resistance,
            support_touches=sup_touches,
            resistance_touches=res_touches,
            inside_ratio=inside_ratio,
            width_atr=float(width_atr),
        )
        # Из подходящих берём самый длинный: чем дольше диапазон, тем больше
        # в нём накопленной ликвидности и тем значимее его границы.
        if best is None or candidate.bars > best.bars:
            best = candidate
    return best


def find_events(
    candles: Sequence[Candle],
    trading_range: TradingRange,
    atr_values: Sequence[Decimal | None],
    rel_volumes: Sequence[float | None],
    params: WyckoffParams,
) -> list[WyckoffEvent]:
    """События §8.4 внутри найденного диапазона."""
    events: list[WyckoffEvent] = []
    for i in range(trading_range.start_index, trading_range.end_index + 1):
        candle = candles[i]
        current_atr = atr_values[i]
        if current_atr is None or current_atr <= 0:
            continue
        rel = rel_volumes[i] if i < len(rel_volumes) else None

        # Климакс: аномальный объём и широкий бар на границе.
        if (
            rel is not None
            and rel >= params.climax_relative_volume
            and candle.range >= current_atr * Decimal("1.5")
        ):
            events.append(
                WyckoffEvent(
                    "climax", i, candle.close,
                    f"объём {rel:.1f}× медианы при широком баре", rel,
                )
            )

        # Spring: прокол поддержки на 0.1–0.7 ATR с закрытием обратно внутрь.
        below = trading_range.support - candle.low
        if (
            current_atr * params.spring_atr_min <= below <= current_atr * params.spring_atr_max
            and candle.close > trading_range.support
        ):
            confirmed = i + 1 >= len(candles) or candles[i + 1].close > trading_range.support
            if confirmed:
                events.append(
                    WyckoffEvent(
                        "spring", i, candle.low,
                        f"прокол поддержки на {float(below / current_atr):.2f} ATR "
                        "с возвратом внутрь",
                        rel,
                    )
                )

        # Upthrust — зеркально по сопротивлению.
        above = candle.high - trading_range.resistance
        if (
            current_atr * params.spring_atr_min <= above <= current_atr * params.spring_atr_max
            and candle.close < trading_range.resistance
        ):
            confirmed = i + 1 >= len(candles) or candles[i + 1].close < trading_range.resistance
            if confirmed:
                events.append(
                    WyckoffEvent(
                        "upthrust", i, candle.high,
                        f"прокол сопротивления на {float(above / current_atr):.2f} ATR "
                        "с возвратом внутрь",
                        rel,
                    )
                )

        # SOS / SOW: импульсное закрытие за границей с объёмом и удержанием.
        if (
            candle.close > trading_range.resistance
            and candle.body >= current_atr * params.sos_body_atr
            and rel is not None
            and rel >= params.sos_relative_volume
        ):
            held = all(
                candles[j].close > trading_range.resistance
                for j in range(i + 1, min(len(candles), i + 3))
            )
            if held:
                events.append(
                    WyckoffEvent("sos", i, candle.close, "импульс за сопротивление", rel)
                )
        if (
            candle.close < trading_range.support
            and candle.body >= current_atr * params.sos_body_atr
            and rel is not None
            and rel >= params.sos_relative_volume
        ):
            held = all(
                candles[j].close < trading_range.support
                for j in range(i + 1, min(len(candles), i + 3))
            )
            if held:
                events.append(
                    WyckoffEvent("sow", i, candle.close, "импульс под поддержку", rel)
                )

    # LPS/LPSY только после SOS/SOW и успешного теста (§8.4).
    sos = next((e for e in events if e.kind == "sos"), None)
    sow = next((e for e in events if e.kind == "sow"), None)
    if sos is not None:
        for j in range(sos.index + 1, trading_range.end_index + 1):
            candle = candles[j]
            if (
                candle.low <= trading_range.resistance
                and candle.close > trading_range.resistance
            ):
                events.append(
                    WyckoffEvent("lps", j, candle.low, "тест сопротивления сверху удержан")
                )
                break
    if sow is not None:
        for j in range(sow.index + 1, trading_range.end_index + 1):
            candle = candles[j]
            if candle.high >= trading_range.support and candle.close < trading_range.support:
                events.append(
                    WyckoffEvent("lpsy", j, candle.high, "тест поддержки снизу удержан")
                )
                break
    return events


def _phase(events: Sequence[WyckoffEvent]) -> str:
    kinds = {e.kind for e in events}
    if kinds & {"lps", "lpsy"}:
        return "D"
    if kinds & {"sos", "sow"}:
        return "D"
    if kinds & {"spring", "upthrust"}:
        return "C"
    if "climax" in kinds:
        return "A"
    return "B"


def detect(
    candles: Sequence[Candle],
    *,
    timeframe: str,
    params: WyckoffParams | None = None,
    atr_period: int = 14,
) -> Reading | None:
    """Наблюдение Вайкоффа.

    Возвращает ``None``, если диапазона нет или уверенность ниже порога.
    §10.2: «при confidence < threshold слой не отображается», а §9.1
    запрещает показывать слой, не участвовавший в оценке. Единственный
    способ выполнить оба требования — не выпускать наблюдение вовсе.
    """
    p = params or WyckoffParams()
    if len(candles) < max(p.min_range_bars, atr_period) + 5:
        return None

    atr_values = atr(candles, atr_period)
    swing_points = swings(candles, confirm_bars=2)
    trading_range = find_range(candles, atr_values, swing_points, p)
    if trading_range is None:
        return None

    rel_volumes = relative_volume(candles)
    events = find_events(candles, trading_range, atr_values, rel_volumes, p)
    phase = _phase(events)

    kinds = {e.kind for e in events}
    terms = (
        ConfidenceTerm(
            "range_quality", 0.25,
            min(1.0, (trading_range.support_touches + trading_range.resistance_touches) / 6),
            f"касаний границ: {trading_range.support_touches}+"
            f"{trading_range.resistance_touches}",
        ),
        ConfidenceTerm(
            "inside_ratio", 0.15, trading_range.inside_ratio,
            f"{trading_range.inside_ratio:.0%} закрытий внутри",
        ),
        ConfidenceTerm(
            "sweep", 0.20, 1.0 if kinds & {"spring", "upthrust"} else 0.0,
            "есть Spring/Upthrust" if kinds & {"spring", "upthrust"} else "прокола границы не было",
        ),
        ConfidenceTerm(
            "volume", 0.20, 1.0 if "climax" in kinds else 0.0,
            "климакс объёма подтверждён" if "climax" in kinds else "климакса объёма не было",
        ),
        ConfidenceTerm(
            "test", 0.20, 1.0 if kinds & {"lps", "lpsy"} else 0.0,
            "тест удержан" if kinds & {"lps", "lpsy"} else "теста после импульса не было",
        ),
    )
    confidence = Confidence(terms)
    if confidence.value < p.min_confidence:
        return None

    marks = [
        Mark(
            kind="wyckoff_range",
            timeframe=timeframe,
            start_time=candles[trading_range.start_index].open_time,
            end_time=candles[trading_range.end_index].open_time,
            price_low=trading_range.support,
            price_high=trading_range.resistance,
            label=f"Диапазон, фаза {phase}",
            confidence=confidence.value,
            display_priority=60,
        )
    ]
    kind_to_mark = {
        "spring": "wyckoff_spring",
        "upthrust": "wyckoff_utad",
        "sos": "wyckoff_sos",
        "sow": "wyckoff_sow",
        "climax": "volume_climax",
    }
    for event in events:
        mark_kind = kind_to_mark.get(event.kind)
        if mark_kind is None:
            continue
        candle = candles[event.index]
        marks.append(
            Mark(
                kind=mark_kind,
                timeframe=timeframe,
                start_time=candle.open_time,
                end_time=candle.open_time,
                price_low=candle.low,
                price_high=candle.high,
                label=f"{event.kind.upper()}: {event.detail}",
                confidence=confidence.value,
                display_priority=85,
            )
        )

    return Reading(
        detector="wyckoff",
        version=VERSION,
        evidence_kind=EVIDENCE_KIND,
        summary=f"Диапазон {trading_range.bars} баров, фаза {phase}",
        detail="; ".join(f"{e.kind}: {e.detail}" for e in events) or "событий нет",
        confidence=confidence,
        measures=(
            Measure("range_bars", "Длина диапазона, баров", float(trading_range.bars)),
            Measure("range_width_atr", "Ширина диапазона, ATR", trading_range.width_atr),
            Measure(
                "boundary_touches", "Касаний границ",
                float(trading_range.support_touches + trading_range.resistance_touches),
            ),
            Measure("inside_ratio", "Доля закрытий внутри", trading_range.inside_ratio),
        ),
        marks=tuple(marks),
        payload={"range": trading_range, "events": events, "phase": phase},
    )
