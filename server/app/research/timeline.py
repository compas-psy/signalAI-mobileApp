"""Две временные оси и момент, с которого фактом можно пользоваться (§2.2).

Самая дорогая ошибка в проверке гипотез на истории — заглянуть в будущее.
Она не выглядит как ошибка: числа сходятся, результат красив, и понять, что
модель знала квартальный отчёт за две недели до его публикации, можно
только по коду.

Поэтому у каждого факта две независимые оси. Экономическая отвечает, когда
событие случилось и какой период описывает. Информационная — когда о нём
стало известно и когда его увидели мы. Смешивать их нельзя: выручка за
второй квартал существует с 30 июня, а знать о ней можно с середины
августа.

``tradable_at`` — единственная точка, которую разрешено использовать в
проверке. Считается детерминированно и консервативно: позже из публикации
и первого обнаружения, плюс операционный лаг, плюс переход к следующему
торговому дню. Каждое слагаемое здесь отвечает за свой способ обмануться:

* «позже из двух» — потому что публикация, которую мы увидели через сутки,
  была для нас недоступна эти сутки, чем бы ни был помечен документ;
* операционный лаг — потому что между появлением документа и решением
  проходит время на разбор, и мгновенная реакция в тесте недостижима на
  практике;
* следующий торговый день — потому что факт, опубликованный после закрытия,
  нельзя использовать по цене закрытия того же дня.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

# Операционный лаг: сколько проходит между появлением документа и моментом,
# когда решение по нему могло быть принято.
#
# Сутки, а не час. Отчётность разбирается не мгновенно, а тест, который
# исходит из мгновенной реакции, измеряет недостижимое.
OPERATIONAL_LAG = timedelta(days=1)

# После какого времени по московской бирже факт уже не успевает в этот день.
# 18:40 — конец основной сессии; всё, что позже, работает со следующего дня.
SESSION_CLOSE_UTC = time(15, 40)


@dataclass(frozen=True, slots=True)
class Availability:
    """Когда фактом можно пользоваться и насколько мы в этом уверены."""

    tradable_at: datetime
    publication_time_uncertain: bool
    detail: str


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _next_session(moment: datetime) -> datetime:
    """Ближайший момент, когда рынок мог отреагировать.

    Выходные считаются по календарю недели, а не по торговому календарю
    биржи: точный календарь праздников здесь был бы догадкой, а ошибка в
    консервативную сторону сдвигает решение позже — то есть в безопасную.
    """
    result = moment
    if result.timetz().replace(tzinfo=None) >= SESSION_CLOSE_UTC:
        result = datetime.combine(
            result.date() + timedelta(days=1), time(0, 0), tzinfo=UTC
        )
    while result.weekday() >= 5:
        result = datetime.combine(
            result.date() + timedelta(days=1), time(0, 0), tzinfo=UTC
        )
    return result


def tradable_at(
    *,
    published_at: datetime | None,
    first_seen_at: datetime,
    lag: timedelta = OPERATIONAL_LAG,
) -> Availability:
    """Первая допустимая точка использования факта.

    ``published_at`` может отсутствовать — у многих источников официального
    времени публикации нет вовсе. Тогда берётся время обнаружения, но факт
    помечается флагом: тест на таких данных систематически оптимистичен
    ровно на неизвестную задержку, и знать об этом обязательно.
    """
    seen = _as_utc(first_seen_at)
    if published_at is None:
        base = seen
        uncertain = True
        detail = (
            "официального времени публикации нет: считаем от первого "
            "обнаружения, задержка публикации неизвестна"
        )
    else:
        published = _as_utc(published_at)
        base = max(published, seen)
        uncertain = False
        detail = (
            "публикация опережает обнаружение — считаем от обнаружения"
            if seen > published
            else "считаем от официальной публикации"
        )
    return Availability(
        tradable_at=_next_session(base + lag),
        publication_time_uncertain=uncertain,
        detail=detail,
    )


def visible_at(candidate_tradable_at: datetime, as_of: datetime) -> bool:
    """Был ли факт доступен на момент решения.

    Ровно одна строка, но именно её забывают: точечный запрос обязан
    фильтровать по ``tradable_at <= as_of`` и брать последнюю ревизию
    **внутри** этого среза, а не глобально последнюю (§8.4).
    """
    return _as_utc(candidate_tradable_at) <= _as_utc(as_of)


def freshness(
    age: timedelta, half_life: timedelta
) -> float:
    """Свежесть по периоду полураспада (§11.5).

    ``exp(-ln2 × age / half_life)``: ровно половина через один период.
    Отрицательный возраст (факт из будущего) — это ошибка данных, а не
    сверхсвежесть, поэтому единица сверху.
    """
    if half_life.total_seconds() <= 0:
        return 0.0
    from math import exp, log

    ratio = age.total_seconds() / half_life.total_seconds()
    if ratio <= 0:
        return 1.0
    return float(exp(-log(2) * ratio))


__all__ = [
    "Availability",
    "OPERATIONAL_LAG",
    "SESSION_CLOSE_UTC",
    "freshness",
    "tradable_at",
    "visible_at",
]
