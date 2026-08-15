"""Движок SPREAD: маржинальный спред (ТЗ §13.4).

Направление будущей маржи оценивается через **разницу** между ценой
продукции и корзиной переменных затрат, а не через прогноз одной сырьевой
цены. Разница принципиальная: нефть подорожала — это ничего не говорит о
марже переработчика, пока не известно, что стало с ценой нефтепродуктов,
курсом, энергией и логистикой.

Модель отрасли задаётся конфигурацией с коэффициентами, и у каждого
коэффициента обязано быть основание. §13.4 запрещает подбирать их ради
максимальной корреляции: подобранный коэффициент объясняет прошлое
идеально и не работает дальше.

Две вещи, которые движок обязан различать и которые легко слить.

**Средняя периода, а не спот.** Цена последнего дня месяца — это не цена,
по которой отгружали весь месяц. Спот на пике даёт спред, которого не было.

**Благоприятный спред и снятый спред.** Хорошая разница цен при остановке
производства не превращается в прибыль. Флаг аварии разделяет
``favorable_spread`` и ``capturable_spread``, и это разные утверждения.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from statistics import median

from .base import Component, Evaluation, clamp, q, robust_z, sigmoid

STRATEGY_KEY = "SPREAD"
STRATEGY_VERSION = "1.0.0"

WEIGHTS = {
    "spread_change": 0.55,
    "persistence": 0.25,
    "coverage": 0.20,
}

# Покрытие модели (§13.4): сколько выручки и переменных затрат описано
# прокси. Ниже порога уверенность ограничивается.
MIN_REVENUE_COVERAGE = 0.70
MIN_COST_COVERAGE = 0.60
LOW_COVERAGE_CAP = Decimal("0.55")

# Минимум кварталов для калибровки.
MIN_QUARTERS = 12

# Потолок уверенности для непроверенной модели.
UNCALIBRATED_CAP = Decimal("0.50")

# Вертикально интегрированная группа частично переносит изменение цены
# сырья между своими сегментами. Пока доля внутреннего трансферта не
# измерена, внешний raw-material spread не может быть увереннее 0.50.
VERTICAL_INTEGRATION_CAP = Decimal("0.50")


@dataclass(frozen=True, slots=True)
class Leg:
    """Одна нога модели: цена и коэффициент с основанием."""

    metric: str
    price: Decimal
    coefficient: Decimal
    # Курс, если цена в валюте. None — цена уже в рублях.
    fx: Decimal | None = None

    @property
    def value(self) -> Decimal:
        rate = self.fx if self.fx is not None else Decimal(1)
        return self.price * rate * self.coefficient


@dataclass(frozen=True, slots=True)
class Period:
    """Средние значения за период — не спот (§13.4)."""

    period: str
    products: tuple[Leg, ...] = ()
    inputs: tuple[Leg, ...] = ()
    logistics: Decimal = Decimal(0)
    variable_tax: Decimal = Decimal(0)
    # Производство остановлено или сильно снижено.
    outage: bool = False

    @property
    def revenue_proxy(self) -> Decimal:
        return sum((leg.value for leg in self.products), Decimal(0))

    @property
    def cost_proxy(self) -> Decimal:
        return (
            sum((leg.value for leg in self.inputs), Decimal(0))
            + self.logistics
            + self.variable_tax
        )

    @property
    def unit_margin(self) -> Decimal:
        return self.revenue_proxy - self.cost_proxy


@dataclass
class SpreadResult(Evaluation):
    unit_margin: Decimal | None = None
    unit_margin_year_ago: Decimal | None = None
    capturable: bool = True


def evaluate(
    periods: list[Period],
    *,
    revenue_coverage: float,
    cost_coverage: float,
    calibrated: bool = True,
    contract_lag_periods: int = 0,
    hedged: bool = False,
    vertically_integrated: bool = False,
) -> SpreadResult:
    """Оценить направление будущей маржи.

    ``contract_lag_periods`` сдвигает сравнение: если контракты
    заключаются на месяц вперёд, сегодняшняя разница цен доедет до отчёта
    не сразу, и сравнивать надо со сдвигом.
    """
    result = SpreadResult(
        strategy_key=STRATEGY_KEY, strategy_version=STRATEGY_VERSION
    )

    if len(periods) < 5:
        result.applicable = False
        result.reason_codes.append("spread_insufficient_history")
        result.detail = f"периодов {len(periods)}: сравнивать не с чем"
        return result

    lag = max(0, contract_lag_periods)
    current_index = len(periods) - 1 - lag
    if current_index < 4:
        result.applicable = False
        result.reason_codes.append("spread_insufficient_history")
        result.detail = "лаг контрактов не оставляет сопоставимого периода"
        return result

    now = periods[current_index]
    year_ago = periods[current_index - 4]
    result.unit_margin = q(now.unit_margin, "0.01")
    result.unit_margin_year_ago = q(year_ago.unit_margin, "0.01")

    margins = [p.unit_margin for p in periods[: current_index + 1]]
    change = now.unit_margin - year_ago.unit_margin
    z = robust_z(margins, now.unit_margin)

    # Устойчивость: сколько последних периодов двигались в одну сторону.
    # Один период — это шум; §13.4 требует двух подряд.
    direction_sign = 1 if change > 0 else (-1 if change < 0 else 0)
    persistence = 0
    for a, b in zip(reversed(margins), reversed(margins[:-1])):
        step = a - b
        if direction_sign > 0 and step > 0:
            persistence += 1
        elif direction_sign < 0 and step < 0:
            persistence += 1
        else:
            break

    coverage = min(revenue_coverage / MIN_REVENUE_COVERAGE,
                   cost_coverage / MIN_COST_COVERAGE)

    result.components = [
        Component(
            "spread_change",
            WEIGHTS["spread_change"],
            sigmoid(z) if z is not None else sigmoid(
                change / (abs(year_ago.unit_margin) or Decimal(1))
            ),
            "разница цен и затрат изменилась",
        ),
        Component(
            "persistence",
            WEIGHTS["persistence"],
            q(Decimal(str(clamp(persistence / 2)))),
            "изменение держится несколько периодов",
        ),
        Component(
            "coverage",
            WEIGHTS["coverage"],
            q(Decimal(str(clamp(coverage)))),
            "модель описывает выручку и затраты",
        ),
    ]
    result.score()

    if revenue_coverage < MIN_REVENUE_COVERAGE or cost_coverage < MIN_COST_COVERAGE:
        result.reason_codes.append("spread_low_coverage")
        result.cap_confidence(LOW_COVERAGE_CAP, "spread_low_coverage")
    if not calibrated or len(periods) < MIN_QUARTERS:
        # Непроверенная модель — это гипотеза о гипотезе. Она может быть
        # верной, но опираться на неё как на измерение нельзя.
        result.reason_codes.append("spread_uncalibrated_proxy")
        result.cap_confidence(UNCALIBRATED_CAP, "spread_uncalibrated_proxy")
    if hedged:
        result.reason_codes.append("spread_hedge_book")
    if vertically_integrated:
        # Рост цены сырья для группы может быть нейтральным: то, что одно
        # подразделение переплатило, другое заработало.
        result.reason_codes.append("spread_vertical_integration")
        result.cap_confidence(
            VERTICAL_INTEGRATION_CAP, "spread_vertical_integration"
        )

    # Авария разделяет «спред хороший» и «спред можно снять». Это разные
    # утверждения, и второе — то, ради чего движок существует.
    result.capturable = not now.outage
    if now.outage:
        result.reason_codes.append("spread_outage_not_capturable")
        result.direction = "neutral"
        result.detail = (
            "разница цен благоприятна, но производство остановлено: снять "
            "её нечем"
        )
        return result

    if persistence < 2:
        result.reason_codes.append("spread_single_period")
        result.direction = "neutral"
        result.detail = "изменение держится меньше двух периодов — это шум"
        return result

    result.direction = "positive" if change > 0 else ("negative" if change < 0 else "neutral")
    result.detail = (
        f"удельная маржа {now.unit_margin:.0f} против "
        f"{year_ago.unit_margin:.0f} годом ранее, устойчиво "
        f"{persistence} периода"
    )
    return result


__all__ = [
    "LOW_COVERAGE_CAP",
    "Leg",
    "MIN_COST_COVERAGE",
    "MIN_QUARTERS",
    "MIN_REVENUE_COVERAGE",
    "Period",
    "STRATEGY_KEY",
    "STRATEGY_VERSION",
    "SpreadResult",
    "UNCALIBRATED_CAP",
    "VERTICAL_INTEGRATION_CAP",
    "WEIGHTS",
    "evaluate",
]
