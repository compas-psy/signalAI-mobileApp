"""Общий контракт детекторов.

Детектор — чистая функция от свечей и параметров. Он не знает ни о сети, ни
о базе, ни о том, кто его вызвал. Из этого следуют два практических
свойства: его можно прогнать на рукотворной фикстуре с точными координатами,
и его результат воспроизводим — одни и те же бары дают один и тот же ответ
(§0.3: «Все торговые вычисления сделать детерминированными и
воспроизводимыми»).

Каждое наблюдение несёт **уверенность с разложением**. Одно число 0,82
ничего не объясняет; набор слагаемых показывает, чем именно оно набрано и
какого подтверждения не хватило.

Каждый детектор несёт **версию**. Она уезжает в разметку графика
(UX-ТЗ §10.6 ``detectorVersion``) и в идею: без неё нельзя понять, почему
вчера разметка была одна, а сегодня другая.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

MeasureStatus = Literal["measured", "not_applicable", "missing"]


@dataclass(frozen=True, slots=True)
class Measure:
    """Одно измерение вместе с его происхождением.

    Различие трёх состояний — не педантизм, а разница в деньгах:

    * ``measured`` — посчитано;
    * ``not_applicable`` — у этого инструмента такого не бывает (открытого
      интереса у акции), вес перераспределяется между остальными;
    * ``missing`` — бывает, но не получено (запрос упал), фактор получает
      ноль и флаг качества данных.

    Смешение двух последних превращает отказ источника в подтверждение.
    """

    name: str
    label: str
    value: float | None = None
    status: MeasureStatus = "measured"
    note: str = ""

    @property
    def is_measured(self) -> bool:
        return self.status == "measured" and self.value is not None


@dataclass(frozen=True, slots=True)
class ConfidenceTerm:
    name: str
    weight: float
    value: float  # 0..1
    note: str = ""


@dataclass(frozen=True, slots=True)
class Confidence:
    """Уверенность как взвешенная сумма слагаемых, а не магическое число."""

    terms: tuple[ConfidenceTerm, ...] = ()

    @property
    def value(self) -> float:
        total = sum(t.weight for t in self.terms)
        if total <= 0:
            return 0.0
        return min(1.0, max(0.0, sum(t.weight * t.value for t in self.terms) / total))

    def missing(self) -> list[str]:
        """Чего именно не хватило — для показа в разборе идеи."""
        return [t.name for t in self.terms if t.value < 0.5]


@dataclass(frozen=True, slots=True)
class Mark:
    """Метка на графике до присвоения идентификаторов.

    Поля повторяют ``ChartAnnotation`` (UX-ТЗ §10.6): без времени начала и
    конца, диапазона цены и уверенности метку нельзя ни нарисовать, ни
    оспорить.
    """

    kind: str
    timeframe: str
    start_time: datetime
    end_time: datetime
    price_low: Decimal | None = None
    price_high: Decimal | None = None
    label: str = ""
    confidence: float = 1.0
    display_priority: int = 50


@dataclass(frozen=True, slots=True)
class Reading:
    """Результат работы детектора."""

    detector: str
    version: str
    evidence_kind: str
    summary: str
    detail: str = ""
    confidence: Confidence = field(default_factory=Confidence)
    measures: tuple[Measure, ...] = ()
    marks: tuple[Mark, ...] = ()
    payload: dict = field(default_factory=dict)

    def measure(self, name: str) -> Measure | None:
        return next((m for m in self.measures if m.name == name), None)
