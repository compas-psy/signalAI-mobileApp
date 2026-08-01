"""Движок CAPACITY: дефицит мощности и бенефициары второго порядка (§13.7).

Когда спрос упирается в физическое ограничение выпуска, происходит две
вещи: у производителя появляется ценовая сила, а у поставщиков
оборудования и монтажа — заказы. Вторые часто интереснее первых, потому что
их рынок замечает позже.

Единственное, что отличает дефицит от затоваривания, — подтверждение
спроса. Загрузка растёт и запасы падают? Может быть дефицит, а может —
остановка линии. Цены растут? Может быть дефицит, а может — курс или
налог. Поэтому §13.7 требует минимум трёх компонентов **включая
подтверждение спроса**, и это требование здесь жёсткое, а не желательное.

Обратный случай назван прямо: выпуск растёт вместе с запасами — это
затоваривание, и сигнала не создаёт, какими бы ни были остальные числа.

Бенефициар второго порядка допускается только при доказанной критичности
узла: «эта компания делает похожее оборудование» — не доказательство.
Порог уверенности 0.75 из §13.7 применяется здесь.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .base import Component, Evaluation, clamp, q, robust_z, sigmoid

STRATEGY_KEY = "CAPACITY"
STRATEGY_VERSION = "1.0.0"

WEIGHTS = {
    "utilization": 0.25,
    "inventory_tightness": 0.20,
    "lead_time": 0.20,
    "pricing_power": 0.15,
    "demand_confirmation": 0.20,
}

# Минимум компонентов и обязательный среди них (§13.7).
MIN_COMPONENTS = 3
REQUIRED_COMPONENT = "demand_confirmation"

# Порог уверенности связи для бенефициара второго порядка.
MIN_CRITICALITY = Decimal("0.75")


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    """Состояние звена цепочки на один период."""

    period: str
    utilization: Decimal | None = None
    inventory_days: Decimal | None = None
    lead_time_days: Decimal | None = None
    producer_price: Decimal | None = None
    output: Decimal | None = None
    real_demand_growth: Decimal | None = None
    # Ввод новых мощностей с датой вступления в силу.
    announced_capacity_within_horizon: Decimal | None = None
    expected_demand_growth: Decimal | None = None
    # Цена выросла из-за курса или налога, а не из-за дефицита.
    price_driven_by_fx_or_tax: bool = False
    imports_can_cover: bool = False


@dataclass
class CapacityResult(Evaluation):
    supply_response_gap: Decimal | None = None
    second_order: bool = False


def _series(values: list[Decimal | None]) -> list[Decimal]:
    return [v for v in values if v is not None]


def evaluate(
    history: list[CapacitySnapshot],
    *,
    second_order_criticality: Decimal | None = None,
) -> CapacityResult:
    """Оценить дефицит мощности.

    ``second_order_criticality`` — доказанная критичность узла для
    бенефициара второго порядка. None означает, что оценивается сам
    производитель.
    """
    result = CapacityResult(
        strategy_key=STRATEGY_KEY, strategy_version=STRATEGY_VERSION
    )

    if len(history) < 5:
        result.applicable = False
        result.reason_codes.append("capacity_insufficient_history")
        return result

    now = history[-1]

    utilization = (
        sigmoid(robust_z(_series([h.utilization for h in history]), now.utilization))
        if now.utilization is not None
        and len(_series([h.utilization for h in history])) >= 4
        else None
    )
    tightness = (
        sigmoid(
            robust_z(
                [-v for v in _series([h.inventory_days for h in history])],
                -now.inventory_days,
            )
        )
        if now.inventory_days is not None
        and len(_series([h.inventory_days for h in history])) >= 4
        else None
    )
    lead_time = (
        sigmoid(
            robust_z(_series([h.lead_time_days for h in history]), now.lead_time_days)
        )
        if now.lead_time_days is not None
        and len(_series([h.lead_time_days for h in history])) >= 4
        else None
    )

    # Ценовая сила засчитывается только при положительном реальном спросе.
    # Цена, выросшая от курса или налога, о дефиците не говорит ничего.
    pricing = None
    prices = _series([h.producer_price for h in history])
    if (
        now.producer_price is not None
        and len(prices) >= 4
        and not now.price_driven_by_fx_or_tax
        and now.real_demand_growth is not None
        and now.real_demand_growth > 0
    ):
        pricing = sigmoid(robust_z(prices, now.producer_price))

    demand = (
        q(Decimal(str(clamp(float(now.real_demand_growth) / 0.1))))
        if now.real_demand_growth is not None and now.real_demand_growth > 0
        else None
    )

    result.components = [
        Component("utilization", WEIGHTS["utilization"], utilization,
                  "загрузка мощностей высока"),
        Component("inventory_tightness", WEIGHTS["inventory_tightness"], tightness,
                  "запасов мало"),
        Component("lead_time", WEIGHTS["lead_time"], lead_time,
                  "сроки поставки растянулись"),
        Component("pricing_power", WEIGHTS["pricing_power"], pricing,
                  "цены растут на фоне реального спроса"),
        Component("demand_confirmation", WEIGHTS["demand_confirmation"], demand,
                  "конечный спрос подтверждён"),
    ]

    measured = {c.key for c in result.components if c.measured}
    if REQUIRED_COMPONENT not in measured:
        # Без подтверждения спроса всё остальное объясняется одинаково
        # хорошо остановкой линии.
        result.applicable = False
        result.reason_codes.append("capacity_no_demand_confirmation")
        result.detail = (
            "конечный спрос не подтверждён: высокая загрузка и пустой склад "
            "так же похожи на остановку производства"
        )
        return result
    if len(measured) < MIN_COMPONENTS:
        result.applicable = False
        result.reason_codes.append("capacity_too_few_components")
        result.detail = f"измерено {len(measured)} компонентов из {MIN_COMPONENTS}"
        return result

    result.score()

    # Затоваривание: выпуск растёт вместе с запасами.
    if len(history) >= 2:
        previous = history[-2]
        if (
            now.output is not None
            and previous.output is not None
            and now.output > previous.output
            and now.inventory_days is not None
            and previous.inventory_days is not None
            and now.inventory_days > previous.inventory_days
        ):
            result.reason_codes.append("capacity_inventory_buildup")
            result.direction = "neutral"
            result.detail = (
                "выпуск растёт вместе с запасами: это затоваривание, а не "
                "дефицит"
            )
            return result

    if now.imports_can_cover:
        result.reason_codes.append("capacity_imports_can_cover")

    if (
        now.expected_demand_growth is not None
        and now.announced_capacity_within_horizon is not None
    ):
        result.supply_response_gap = q(
            now.expected_demand_growth - now.announced_capacity_within_horizon
        )
        if result.supply_response_gap <= 0:
            # Новые мощности вводятся в пределах горизонта — дефицит
            # закроется сам.
            result.reason_codes.append("capacity_new_supply_arrives")
            result.direction = "neutral"
            result.detail = (
                "заявленные мощности закрывают ожидаемый спрос в пределах "
                "горизонта"
            )
            return result

    if second_order_criticality is not None:
        result.second_order = True
        if second_order_criticality < MIN_CRITICALITY:
            result.reason_codes.append("capacity_weak_criticality")
            result.cap_confidence(Decimal("0.50"), "capacity_weak_criticality")
            result.detail = (
                "критичность узла для бенефициара второго порядка не доказана"
            )

    result.direction = "positive"
    result.detail = (
        "спрос упирается в ограничение выпуска: "
        + ", ".join(sorted(measured))
    )
    return result


__all__ = [
    "CapacityResult",
    "CapacitySnapshot",
    "MIN_COMPONENTS",
    "MIN_CRITICALITY",
    "REQUIRED_COMPONENT",
    "STRATEGY_KEY",
    "STRATEGY_VERSION",
    "WEIGHTS",
    "evaluate",
]
