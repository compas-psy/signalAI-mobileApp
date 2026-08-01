"""Общий контракт движков ранних сигналов (ТЗ §13).

Движок — чистая функция. Он не ходит в сеть, не запускает модель и не
читает «последние» данные: ему передают срез на момент решения, и он
возвращает оценку. Это не архитектурная гигиена, а условие §2.7:
повторный запуск на тех же входах обязан дать те же числа, иначе
проверить систему на истории невозможно в принципе.

Отсюда же общая форма результата. Каждый движок возвращает направление,
силу, уверенность и **компоненты с весами** — потому что §2.6 запрещает
один балл без расшифровки. Компонент, который не удалось измерить, не
исчезает: он остаётся в списке со значением ``None``, и его вес не
перераспределяется между остальными. Иначе одна раскрытая строка отчёта
давала бы такую же силу, как полный отчёт.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from math import exp
from statistics import median

EPS = Decimal("1e-12")


def q(value: Decimal | float, places: str = "0.0001") -> Decimal:
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def robust_z(series: list[Decimal], value: Decimal) -> Decimal | None:
    """Устойчивый z-счёт по медиане и MAD (§11.3).

    Через медиану, а не через среднее: одна разовая сделка сдвигает среднее
    так, что нормальный период выглядит выбросом. При нулевом MAD переходим
    на межквартильный размах; если и он ноль — счёт не считается вовсе.
    Ноль вместо него означал бы «изменений нет», а правда в том, что мерить
    нечем.
    """
    if len(series) < 4:
        return None
    med = Decimal(str(median(float(x) for x in series)))
    deviations = sorted(abs(x - med) for x in series)
    scale = Decimal("1.4826") * Decimal(str(median(float(x) for x in deviations)))
    if scale <= 0:
        ordered = sorted(series)
        n = len(ordered)
        scale = (ordered[int(n * 0.75) - 1] - ordered[int(n * 0.25)]) / Decimal(2)
    if scale <= 0:
        return None
    z = (value - med) / (scale + EPS)
    return max(Decimal(-5), min(Decimal(5), z))


def sigmoid(z: Decimal, scale: float = 1.5) -> Decimal:
    """Перевод z-счёта в [0,1]. Масштаб 1.5 задан §13.1 и общий для движков."""
    return q(1.0 / (1.0 + exp(-float(z) / scale)))


def real_growth(nominal: Decimal | None, inflation: Decimal | None) -> Decimal | None:
    """Рост за вычетом инфляции (§11.4).

    ``(1 + nominal) / (1 + inflation) - 1``. Двадцать процентов роста при
    двадцати пяти процентах инфляции — это падение, и разница между
    номинальным и реальным здесь не оттенок, а знак.
    """
    if nominal is None or inflation is None:
        return None
    denominator = Decimal(1) + inflation
    if denominator == 0:
        return None
    return (Decimal(1) + nominal) / denominator - Decimal(1)


@dataclass(frozen=True, slots=True)
class Component:
    """Один компонент оценки: вес, значение и за что он отвечает."""

    key: str
    weight: float
    value: Decimal | None
    note: str = ""

    @property
    def measured(self) -> bool:
        return self.value is not None


@dataclass
class Evaluation:
    """Итог движка.

    ``applicable=False`` означает «этот вопрос к данному объекту не
    относится» и отличается от нулевой силы: к банку неприменим оборотный
    капитал, а не «у банка он плохой».
    """

    strategy_key: str
    strategy_version: str
    applicable: bool = True
    direction: str = "neutral"
    strength: Decimal = Decimal(0)
    confidence: Decimal = Decimal(0)
    components: list[Component] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    detail: str = ""
    metrics: dict[str, Decimal] = field(default_factory=dict)

    def score(self, *, data_quality: Decimal = Decimal(1)) -> None:
        """Свести компоненты в силу и уверенность.

        Вес неизмеренного компонента не перераспределяется: падает
        уверенность, а не растут остальные.
        """
        measured = [c for c in self.components if c.measured]
        if not measured:
            self.applicable = False
            self.strength = Decimal(0)
            self.confidence = Decimal(0)
            return
        self.strength = q(
            sum(Decimal(str(c.weight)) * (c.value or Decimal(0)) for c in measured)
        )
        covered = Decimal(str(sum(c.weight for c in measured)))
        self.confidence = q(covered * max(Decimal(0), data_quality))

    def cap_confidence(self, ceiling: Decimal, code: str) -> None:
        """Ограничить уверенность сверху с указанием причины."""
        if self.confidence > ceiling:
            self.confidence = ceiling
            if code not in self.reason_codes:
                self.reason_codes.append(code)


__all__ = [
    "Component",
    "EPS",
    "Evaluation",
    "clamp",
    "q",
    "real_growth",
    "robust_z",
    "sigmoid",
]
