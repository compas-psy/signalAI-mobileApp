"""Индикаторы и структура свингов (engine-ТЗ §8.3, §9).

Величины, имеющие размерность цены (ATR, EMA), считаются в ``Decimal``: из
них строится стоп, а стоп — это деньги. Безразмерные (ADX, RSI, z-оценка,
перцентиль) считаются во ``float``: там точность двоичной дроби никого не
ранит, а читаемость выигрывает.

Главное правило файла — **никакого заглядывания в будущее**. Свинг
подтверждается только после k последующих баров (§8.3), и функция
подтверждения возвращает не «где экстремум», а «где экстремум, о котором мы
уже знали к этому моменту». Разница между этими двумя ответами и есть та
самая ошибка, из-за которой бэктест красив, а торговля убыточна.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..market.candles import Candle


# ─── Скользящие ───────────────────────────────────────────────────────────


def ema(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """Экспоненциальная средняя.

    Первое значение — простое среднее первых ``period`` элементов: стартовать
    с первого значения ряда значит десятки баров показывать не среднюю, а
    почти саму цену.
    """
    if period <= 0:
        raise ValueError("period должен быть положительным")
    out: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = Decimal(2) / Decimal(period + 1)
    seed = sum(values[:period]) / Decimal(period)
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = (values[i] - prev) * k + prev
        out[i] = prev
    return out


def true_range(candles: Sequence[Candle]) -> list[Decimal]:
    """Истинный диапазон. Первый бар — просто его размах: предыдущего нет."""
    out: list[Decimal] = []
    for i, c in enumerate(candles):
        if i == 0:
            out.append(c.high - c.low)
            continue
        prev_close = candles[i - 1].close
        out.append(
            max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
        )
    return out


def atr(candles: Sequence[Candle], period: int = 14) -> list[Decimal | None]:
    """ATR по Уайлдеру (сглаживание 1/period, а не 2/(period+1)).

    Это важно: «ATR(14)» в разных программах означает разные числа, а у нас
    от него зависит буфер стопа (§10.4: «stop за sweep + max(0.1 ATR, 2
    ticks)»).
    """
    if period <= 0:
        raise ValueError("period должен быть положительным")
    tr = true_range(candles)
    out: list[Decimal | None] = [None] * len(candles)
    if len(candles) < period:
        return out
    seed = sum(tr[:period]) / Decimal(period)
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(candles)):
        prev = (prev * Decimal(period - 1) + tr[i]) / Decimal(period)
        out[i] = prev
    return out


def rsi(candles: Sequence[Candle], period: int = 14) -> list[float | None]:
    """RSI по Уайлдеру.

    В восьми факторах оценки RSI нет; он остаётся вторичным контекстом и на
    балл не влияет (см. docs/TZ_PRIORITY.md).
    """
    closes = [c.close for c in candles]
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    for prev, curr in zip(closes, closes[1:], strict=False):
        delta = curr - prev
        gains.append(max(Decimal(0), delta))
        losses.append(max(Decimal(0), -delta))

    avg_gain = sum(gains[:period]) / Decimal(period)
    avg_loss = sum(losses[:period]) / Decimal(period)

    def value(g: Decimal, loss: Decimal) -> float:
        if loss == 0:
            return 100.0
        rs = g / loss
        return float(100 - (100 / (1 + rs)))

    out[period] = value(avg_gain, avg_loss)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / Decimal(period)
        avg_loss = (avg_loss * (period - 1) + losses[i]) / Decimal(period)
        out[i + 1] = value(avg_gain, avg_loss)
    return out


def adx(candles: Sequence[Candle], period: int = 14) -> list[float | None]:
    """ADX по Уайлдеру — сила тренда без учёта направления (§9.1)."""
    n = len(candles)
    out: list[float | None] = [None] * n
    if n < period * 2:
        return out

    tr = true_range(candles)
    plus_dm: list[Decimal] = [Decimal(0)]
    minus_dm: list[Decimal] = [Decimal(0)]
    for i in range(1, n):
        up = candles[i].high - candles[i - 1].high
        down = candles[i - 1].low - candles[i].low
        plus_dm.append(up if up > down and up > 0 else Decimal(0))
        minus_dm.append(down if down > up and down > 0 else Decimal(0))

    def wilder(series: list[Decimal]) -> list[Decimal | None]:
        smoothed: list[Decimal | None] = [None] * n
        if n <= period:
            return smoothed
        acc = sum(series[1 : period + 1])
        smoothed[period] = acc
        for i in range(period + 1, n):
            acc = acc - acc / Decimal(period) + series[i]
            smoothed[i] = acc
        return smoothed

    str_ = wilder(tr)
    spdm = wilder(plus_dm)
    smdm = wilder(minus_dm)

    dx: list[float | None] = [None] * n
    for i in range(period, n):
        if not str_[i] or str_[i] == 0:
            continue
        pdi = float(spdm[i] / str_[i]) * 100
        mdi = float(smdm[i] / str_[i]) * 100
        total = pdi + mdi
        dx[i] = 0.0 if total == 0 else abs(pdi - mdi) / total * 100

    first = period * 2 - 1
    window = [d for d in dx[period:first + 1] if d is not None]
    if len(window) < period:
        return out
    prev = sum(window) / len(window)
    out[first] = prev
    for i in range(first + 1, n):
        if dx[i] is None:
            continue
        prev = (prev * (period - 1) + dx[i]) / period
        out[i] = prev
    return out


# ─── Структура: свинги с подтверждением ───────────────────────────────────


@dataclass(frozen=True, slots=True)
class Swing:
    """Подтверждённый экстремум.

    ``index`` — где экстремум на самом деле; ``confirmed_index`` — бар, на
    котором мы узнали о нём. Второе больше первого ровно на k, и именно им
    обязаны пользоваться и стратегия, и бэктест.
    """

    index: int
    confirmed_index: int
    price: Decimal
    is_high: bool


def swings(
    candles: Sequence[Candle], *, confirm_bars: int = 2, min_move: Decimal | None = None
) -> list[Swing]:
    """Найти свинги, подтверждённые ``confirm_bars`` последующими барами.

    §8.3: «Pivot high/low подтверждается только после k последующих баров».
    Экстремум, у которого справа ещё нет k баров, не возвращается вовсе — не
    «возвращается с пометкой», а не возвращается: иначе им кто-нибудь
    воспользуется.

    ``min_move`` отбрасывает мелкие зубцы: на 4H фрактал глубины 2 без
    фильтра даёт шум, из которого не построить ни трендовой, ни структуры.
    """
    k = confirm_bars
    if k < 1:
        raise ValueError("confirm_bars должно быть не меньше 1")
    found: list[Swing] = []
    for i in range(k, len(candles) - k):
        window_before = candles[i - k : i]
        window_after = candles[i + 1 : i + 1 + k]
        c = candles[i]
        if all(c.high > b.high for b in window_before) and all(
            c.high > a.high for a in window_after
        ):
            found.append(Swing(i, i + k, c.high, True))
        elif all(c.low < b.low for b in window_before) and all(
            c.low < a.low for a in window_after
        ):
            found.append(Swing(i, i + k, c.low, False))

    if min_move is None or min_move <= 0:
        return found

    # Оставляем чередование high/low и выбрасываем экстремумы, не давшие
    # движения хотя бы на min_move от предыдущего противоположного.
    filtered: list[Swing] = []
    for s in found:
        if not filtered:
            filtered.append(s)
            continue
        last = filtered[-1]
        if last.is_high == s.is_high:
            # Два подряд в одну сторону — оставляем более выраженный.
            better = (s.price > last.price) if s.is_high else (s.price < last.price)
            if better:
                filtered[-1] = s
            continue
        if abs(s.price - last.price) >= min_move:
            filtered.append(s)
    return filtered


def swings_known_at(swings_: Sequence[Swing], bar_index: int) -> list[Swing]:
    """Свинги, о которых было известно к бару ``bar_index``.

    Единственный способ не заглянуть в будущее при прогоне по истории.
    """
    return [s for s in swings_ if s.confirmed_index <= bar_index]


def structure_direction(swings_: Sequence[Swing]) -> str:
    """HH/HL против LH/LL (§9.1).

    Возвращает ``up``, ``down`` или ``flat``. Нужны минимум два максимума и
    два минимума: по одной паре структуру не определить.
    """
    highs = [s for s in swings_ if s.is_high][-2:]
    lows = [s for s in swings_ if not s.is_high][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return "flat"
    higher_high = highs[-1].price > highs[-2].price
    higher_low = lows[-1].price > lows[-2].price
    lower_high = highs[-1].price < highs[-2].price
    lower_low = lows[-1].price < lows[-2].price
    if higher_high and higher_low:
        return "up"
    if lower_high and lower_low:
        return "down"
    return "flat"


# ─── Объём и перцентили ───────────────────────────────────────────────────


def relative_volume(
    candles: Sequence[Candle], *, window: int = 20
) -> list[float | None]:
    """Объём к медиане окна.

    Медиана, а не среднее: один климакс сдвигает среднее и обесценивает шкалу
    на всё окно вперёд — ровно тогда, когда объём важнее всего.
    """
    out: list[float | None] = [None] * len(candles)
    for i in range(len(candles)):
        if i + 1 < window:
            continue
        vols = [
            float(c.volume_units)
            for c in candles[i + 1 - window : i + 1]
            if c.volume_units is not None
        ]
        current = candles[i].volume_units
        if current is None or len(vols) < window // 2:
            continue
        med = statistics.median(vols)
        if med > 0:
            out[i] = float(current) / med
    return out


def zscore(values: Sequence[float], *, window: int = 20) -> list[float | None]:
    """Скользящая z-оценка. Нулевое стандартное отклонение — не бесконечность."""
    out: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if i + 1 < window:
            continue
        chunk = list(values[i + 1 - window : i + 1])
        mean = statistics.fmean(chunk)
        sd = statistics.pstdev(chunk)
        out[i] = 0.0 if sd == 0 else (values[i] - mean) / sd
    return out


def percentile_rank(values: Sequence[float], current: float) -> float:
    """Позиция значения в выборке, 0–100 (§9.2)."""
    if not values:
        return 50.0
    below = sum(1 for v in values if v < current)
    equal = sum(1 for v in values if v == current)
    return (below + equal / 2) / len(values) * 100
