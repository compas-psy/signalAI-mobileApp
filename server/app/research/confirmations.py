"""Подтверждения между периодами (§12.3).

Подтверждение — это когда то же самое повторилось в другом экономическом
периоде. Не когда мы ещё раз запустили сбор, не когда источник прислал тот
же период заново, не когда значение уточнили ревизией и не когда в одном
периоде оказалось несколько строк.

Разница не педантичная. Раньше `confirmations` был захардкожен единицей, и
любая замена этой единицы на «сколько раз мы видели факт» превратила бы
повторный ingestion в подтверждение — то есть система подтверждала бы
гипотезы собственной активностью.

Про минимальную историю. Годовое сравнение требует года истории **до**
первого сравнения, и только потом начинают набираться подтверждения:

    required_periods = seasonal_lag + confirmation_periods

Месячный ряд с двумя подтверждениями — 14 периодов, квартальный — 6,
годовой — 3. Общий порог в пять периодов, стоявший раньше, был одинаково
неверен для всех трёх: месячному ряду его мало, годовому много.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

#: Сколько периодов назад лежит сопоставимый период. Год — но выраженный
#: в шагах самого ряда.
SEASONAL_LAG: dict[str, int] = {
    "monthly": 12,
    "quarterly": 4,
    "yearly": 1,
    "annual": 1,
    "weekly": 52,
    "daily": 365,
}

#: Сколько подтверждений требует правило 3–2–1.
DEFAULT_CONFIRMATIONS = 2

#: Сигнал слабее этого в подтверждение не засчитывается: колебание около
#: нуля повторяется само по себе и подтверждает только шум.
MIN_STRENGTH = Decimal("0.2")

#: Этапы события считаются разными, только если разнесены во времени.
#: Извещение и заключение контракта в один день — одно решение, а не два
#: подтверждения.
MIN_EVENT_GAP = timedelta(days=7)


def required_periods(
    frequency: str, confirmations: int = DEFAULT_CONFIRMATIONS
) -> int:
    """Сколько периодов истории нужно, чтобы вообще начать подтверждать."""
    lag = SEASONAL_LAG.get((frequency or "").strip().lower(), 4)
    return lag + max(0, confirmations)


@dataclass
class Confirmation:
    """Одно засчитанное подтверждение."""

    period_end: date
    direction: str
    strength: Decimal


@dataclass
class ConfirmationCount:
    """Сколько подтверждений набралось и почему остальные не в счёт."""

    confirmations: list[Confirmation] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)

    @property
    def periods(self) -> int:
        return len(self.confirmations)

    def _reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


@dataclass(frozen=True, slots=True)
class Reading:
    """Одно измерение сигнала: период, направление, сила."""

    fingerprint: str
    period_end: date
    direction: str
    strength: Decimal
    #: Номер ревизии. Уточнённое значение того же периода подтверждением
    #: не является: это тот же факт, рассказанный точнее.
    revision: int = 0


def count(readings: list[Reading], *, min_strength: Decimal = MIN_STRENGTH) -> ConfirmationCount:
    """Сколько различных экономических периодов подтверждают одно и то же.

    Считаются только совпадающие по отпечатку и направлению показания
    достаточной силы, и по одному на период. Всё остальное попадает в
    `rejected` с причиной — молча отбрасывать здесь нельзя, иначе «почему
    не подтверждается» останется без ответа.
    """
    result = ConfirmationCount()
    if not readings:
        return result

    # Отпечаток берётся у самого свежего: гипотеза — про сегодняшнюю
    # конфигурацию сигналов, а не про историческую.
    ordered = sorted(readings, key=lambda r: (r.period_end, r.revision))
    head = ordered[-1]
    seen: dict[date, Reading] = {}

    for reading in ordered:
        if reading.fingerprint != head.fingerprint:
            result._reject("другой отпечаток сигнала")
            continue
        if reading.direction != head.direction:
            result._reject("направление не совпадает")
            continue
        if abs(reading.strength) < min_strength:
            result._reject("сила ниже порога")
            continue
        previous = seen.get(reading.period_end)
        if previous is not None:
            # Тот же период: ревизия, повторная загрузка или вторая строка.
            # Все три — один и тот же экономический факт.
            result._reject(
                "ревизия того же периода"
                if reading.revision > previous.revision
                else "повтор того же периода"
            )
            continue
        seen[reading.period_end] = reading

    result.confirmations = [
        Confirmation(period_end=p, direction=r.direction, strength=r.strength)
        for p, r in sorted(seen.items())
    ]
    return result


def count_event_stages(
    stages: list[tuple[date, str]], *, min_gap: timedelta = MIN_EVENT_GAP
) -> ConfirmationCount:
    """Подтверждения для событийного сигнала.

    У события нет периодов — у него есть стадии исполнения. Засчитываются
    последовательные стадии, разнесённые не менее чем на неделю: извещение
    и заключение в один день — одно решение, а не два подтверждения.
    """
    result = ConfirmationCount()
    last: date | None = None
    for when, stage in sorted(stages):
        if last is not None and when - last < min_gap:
            result._reject("стадии ближе недели")
            continue
        last = when
        result.confirmations.append(
            Confirmation(period_end=when, direction=stage, strength=Decimal(1))
        )
    return result


def history_verdict(
    unique_periods: int, frequency: str, confirmations: int = DEFAULT_CONFIRMATIONS
) -> tuple[bool, str]:
    """Хватает ли истории — и если нет, то насколько именно."""
    need = required_periods(frequency, confirmations)
    if unique_periods >= need:
        return True, ""
    return False, (
        f"истории {unique_periods} из {need} периодов "
        f"({frequency}: {SEASONAL_LAG.get(frequency, 4)} на сравнение "
        f"с прошлым годом плюс {confirmations} на подтверждения)"
    )


__all__ = [
    "Confirmation",
    "ConfirmationCount",
    "DEFAULT_CONFIRMATIONS",
    "MIN_EVENT_GAP",
    "MIN_STRENGTH",
    "Reading",
    "SEASONAL_LAG",
    "count",
    "count_event_stages",
    "history_verdict",
    "required_periods",
]
