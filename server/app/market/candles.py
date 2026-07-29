"""Каноническая свеча и построение старших таймфреймов (engine-ТЗ §4.3, §4.4).

Здесь сосредоточены три запрета §4.4, которые дороже всего нарушить:

1. **Незакрытая свеча не используется как закрытая.** Признак ``is_closed``
   несёт сама свеча, а ресемпл по умолчанию выбрасывает последнее неполное
   ведро: иначе структура «мерцает» между двумя пересчётами, и детектор
   находит слом, которого через час не будет.
2. **Ведро строится от открытия сессии, а не от полуночи.** На FORTS торги
   начинаются в 09:00 МСК. Деление по ``hour // 4`` от полуночи даёт первым
   ведром дня огрызок из одного часа — и Вайкофф видит фальшиво узкий бар,
   а ATR занижается.
3. **Открытый интерес не выдумывается.** При склейке берётся значение
   последней свечи ведра, у которой оно есть; если его нет ни у одной, ведро
   честно остаётся без OI и получает флаг.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ..models.enums import QualityFlag, Timeframe

# Московская биржа живёт в UTC+3; торговый день начинается в 09:00 МСК.
MSK = timedelta(hours=3)
MOEX_SESSION_START_HOUR_UTC = 6  # 09:00 МСК


@dataclass(frozen=True, slots=True)
class Candle:
    """Свеча в канонической форме §4.3.

    Цены — ``Decimal``. Объём и OI необязательны: у части инструментов их
    просто не существует, и ноль вместо отсутствия — выдуманное значение.
    """

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_units: Decimal | None = None
    volume_notional: Decimal | None = None
    open_interest: Decimal | None = None
    is_closed: bool = True
    source: str = ""
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is None:
            raise ValueError("время свечи обязано быть с зоной: §4.4 требует UTC")
        if self.high < self.low:
            raise ValueError(f"high {self.high} ниже low {self.low}")
        if not (self.low <= self.open <= self.high):
            raise ValueError("open вне диапазона high/low")
        if not (self.low <= self.close <= self.high):
            raise ValueError("close вне диапазона high/low")

    @property
    def range(self) -> Decimal:
        return self.high - self.low

    @property
    def body(self) -> Decimal:
        return abs(self.close - self.open)

    @property
    def is_up(self) -> bool:
        return self.close > self.open

    @property
    def is_down(self) -> bool:
        return self.close < self.open

    @property
    def upper_wick(self) -> Decimal:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> Decimal:
        return min(self.open, self.close) - self.low

    def with_flag(self, flag: QualityFlag) -> "Candle":
        if flag.value in self.quality_flags:
            return self
        return replace(self, quality_flags=(*self.quality_flags, flag.value))


TIMEFRAME_MINUTES: dict[Timeframe, int] = {
    Timeframe.M10: 10,
    Timeframe.M15: 15,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
}


def closed_only(candles: Sequence[Candle]) -> list[Candle]:
    """Оставить только закрытые свечи (§4.4).

    Не «отрезать последнюю»: незакрытой может оказаться не только последняя,
    если данные пришли из нескольких источников.
    """
    return [c for c in candles if c.is_closed]


def _merge(
    bucket: Sequence[Candle], start: datetime, *, closed: bool, complete: bool
) -> Candle:
    """Склеить ведро в одну свечу.

    Два признака, которые нельзя смешивать:

    * ``closed`` — период **закончился**. Такую свечу можно считать.
    * ``complete`` — в ведре все составляющие бары. Ведро в начале истории
      закончилось давно, но собрано из двух часов вместо четырёх: считать его
      можно, а вот молчать об этом нельзя — уровни и ATR по нему занижены.

    Объём складывается; OI берётся последний известный — он не аддитивен, это
    состояние рынка на конец периода, а не поток за период.
    """
    volumes = [c.volume_units for c in bucket if c.volume_units is not None]
    notionals = [c.volume_notional for c in bucket if c.volume_notional is not None]
    ois = [c.open_interest for c in bucket if c.open_interest is not None]

    flags: list[str] = []
    for c in bucket:
        for f in c.quality_flags:
            if f not in flags:
                flags.append(f)
    if not ois and QualityFlag.OI_UNAVAILABLE.value not in flags:
        flags.append(QualityFlag.OI_UNAVAILABLE.value)
    if not complete and QualityFlag.PARTIAL_BAR.value not in flags:
        flags.append(QualityFlag.PARTIAL_BAR.value)

    return Candle(
        open_time=start,
        open=bucket[0].open,
        high=max(c.high for c in bucket),
        low=min(c.low for c in bucket),
        close=bucket[-1].close,
        volume_units=sum(volumes) if volumes else None,
        volume_notional=sum(notionals) if notionals else None,
        open_interest=ois[-1] if ois else None,
        is_closed=closed,
        source=bucket[-1].source,
        quality_flags=tuple(flags),
    )


def resample_hours(
    candles: Sequence[Candle],
    hours: int,
    *,
    session_start_hour_utc: int = 0,
    drop_forming: bool = True,
) -> list[Candle]:
    """Склеить часовые свечи в ``hours``-часовые.

    ``session_start_hour_utc`` задаёт точку отсчёта ведра. Для FORTS это 06:00
    UTC (09:00 МСК): без этого первое ведро торгового дня состоит из одного
    часа, и весь расчёт диапазонов и ATR на 4H смещается.

    ``drop_forming`` выбрасывает последнее ведро, если оно не набрало полного
    числа часов или содержит незакрытую свечу — §4.4.
    """
    if hours <= 0:
        raise ValueError("hours должно быть положительным")
    source = sorted(candles, key=lambda c: c.open_time)
    if not source:
        return []

    buckets: dict[datetime, list[Candle]] = {}
    order: list[datetime] = []
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    for candle in source:
        # Считаем в абсолютных часах от эпохи, сдвинув начало отсчёта на час
        # открытия сессии. Так граница ведра не зависит ни от календарного
        # дня, ни от перехода через полночь.
        shifted = candle.open_time - timedelta(hours=session_start_hour_utc)
        epoch_hours = int((shifted - epoch).total_seconds() // 3600)
        start = (
            epoch
            + timedelta(hours=(epoch_hours // hours) * hours)
            + timedelta(hours=session_start_hour_utc)
        )
        if start not in buckets:
            buckets[start] = []
            order.append(start)
        buckets[start].append(candle)

    result: list[Candle] = []
    for i, start in enumerate(order):
        bucket = buckets[start]
        is_last = i == len(order) - 1
        complete = len(bucket) == hours
        # Период закончился, если ведро не последнее (есть данные позже) либо
        # оно набрало полный размер. Незакрытый бар внутри закрывает вопрос
        # сразу: такое ведро считать нельзя.
        closed = all(c.is_closed for c in bucket) and (not is_last or complete)
        if is_last and drop_forming and not closed:
            continue
        result.append(_merge(bucket, start, closed=closed, complete=complete))
    return result


def resample_days_to(
    candles: Sequence[Candle], period: str, *, drop_forming: bool = True
) -> list[Candle]:
    """Склеить дневные свечи в недельные (``week``) или месячные (``month``).

    Нужны для макроконтекста §7: «1M используется только для макроуровней».
    Отдельного сетевого запроса под них нет и не должно быть — это
    производные ряды.
    """
    if period not in ("week", "month"):
        raise ValueError("period может быть 'week' или 'month'")
    source = sorted(candles, key=lambda c: c.open_time)
    if not source:
        return []

    def key(candle: Candle):
        day = candle.open_time
        if period == "month":
            return (day.year, day.month)
        monday = day - timedelta(days=day.weekday())
        return (monday.year, monday.month, monday.day)

    buckets: dict[tuple, list[Candle]] = {}
    order: list[tuple] = []
    for candle in source:
        k = key(candle)
        if k not in buckets:
            buckets[k] = []
            order.append(k)
        buckets[k].append(candle)

    expected = {"week": 5, "month": 18}[period]
    result: list[Candle] = []
    for i, k in enumerate(order):
        bucket = buckets[k]
        start = bucket[0].open_time
        is_last = i == len(order) - 1
        # Полноту недели и месяца по числу баров не определить точно:
        # праздники и короткие сессии легальны. Поэтому порог мягкий и
        # применяется только к последнему ведру — только оно может быть ещё
        # не прожитым.
        complete = len(bucket) >= expected
        closed = all(c.is_closed for c in bucket) and (not is_last or complete)
        if is_last and drop_forming and not closed:
            continue
        result.append(_merge(bucket, start, closed=closed, complete=complete))
    return result


def detect_gaps(
    candles: Sequence[Candle], timeframe: Timeframe, *, max_missing: int = 0
) -> list[tuple[datetime, datetime]]:
    """Найти дыры в ряду (флаг ``GAP``, §4.3).

    Возвращает пары «после какой свечи» → «перед какой». Календарь торгов
    здесь не учитывается: сама по себе дыра — это факт, а решение, законна ли
    она (выходной, клиринг), принимает адаптер площадки, который знает
    расписание.
    """
    minutes = TIMEFRAME_MINUTES.get(timeframe)
    if minutes is None:
        return []
    step = timedelta(minutes=minutes)
    holes: list[tuple[datetime, datetime]] = []
    ordered = sorted(candles, key=lambda c: c.open_time)
    for prev, curr in zip(ordered, ordered[1:], strict=False):
        missing = int((curr.open_time - prev.open_time) / step) - 1
        if missing > max_missing:
            holes.append((prev.open_time, curr.open_time))
    return holes


def is_stale(
    candles: Sequence[Candle],
    timeframe: Timeframe,
    now: datetime,
    *,
    max_age_seconds: int,
) -> bool:
    """Устарел ли ряд (флаг ``STALE``, §21 kill switch).

    Возраст считается от **конца** последней свечи, а не от её открытия:
    часовая свеча, открытая час назад, только что закрылась и свежа.
    """
    if not candles:
        return True
    last = max(candles, key=lambda c: c.open_time)
    minutes = TIMEFRAME_MINUTES.get(timeframe, 0)
    end = last.open_time + timedelta(minutes=minutes)
    return (now - end).total_seconds() > max_age_seconds
