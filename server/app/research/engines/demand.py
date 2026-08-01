"""Движок DEMAND: интерес → реальные траты → физический объём (ТЗ §13.2).

Вопрос: ускоряется ли конечный спрос — и отличается ли он от медийного
интереса и от инфляции.

Три компонента, и их порядок в весах не случаен. Поисковый интерес весит
меньше всех (0.25), потому что он самый ранний и самый ненадёжный: реклама
поднимает запросы, скандал поднимает запросы, любопытство поднимает
запросы. Реальные траты весят больше (0.35): это уже деньги, но их можно
раздуть инфляцией. Физический объём весит больше всех (0.40), потому что
штуки нельзя переоценить — тонна остаётся тонной при любой ставке.

Отсюда главное правило движка: **без физического объёма экономическая
оценка ограничена сверху** (§13.2). Рост запросов и рост номинальных трат,
не подтверждённые штуками, — это гипотеза о внимании, а не о спросе.

Согласованность компонентов входит в силу отдельным множителем. Три
показателя, разошедшиеся в разные стороны, — это не «в среднем рост», а
признак того, что мы измеряем разные вещи.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from statistics import median, pstdev

from .base import (
    Component,
    Evaluation,
    clamp,
    q,
    real_growth,
    robust_z,
    sigmoid,
)

STRATEGY_KEY = "DEMAND"
STRATEGY_VERSION = "1.0.0"

WEIGHTS = {
    "search_acceleration": 0.25,
    "real_spend_growth": 0.35,
    "physical_volume": 0.40,
}

# Потолок экономической оценки без физического объёма (§13.2).
NO_VOLUME_CAP = Decimal("0.65")

# Сколько компонентов должны смотреть в одну сторону для подтверждения.
MIN_ALIGNED = 2


@dataclass(frozen=True, slots=True)
class DemandPeriod:
    """Один период наблюдения спроса."""

    period: str
    search_volume: Decimal | None = None
    nominal_spend: Decimal | None = None
    physical_volume: Decimal | None = None
    category_inflation: Decimal | None = None
    # Маркеры, меняющие смысл чисел.
    ad_campaign: bool = False
    news_spike: bool = False
    out_of_stock: bool = False
    unit_price_change: Decimal | None = None
    margin_change: Decimal | None = None


@dataclass
class DemandResult(Evaluation):
    aligned: int = 0
    alignment: Decimal = Decimal(0)
    economic_cap: Decimal | None = None


def _series_growth(values: list[Decimal | None]) -> Decimal | None:
    """Рост последнего периода к тому же периоду годом ранее.

    Год назад, а не к предыдущему кварталу: у спроса сезонность сильнее
    тренда, и сравнение с декабрём даёт январский провал у всех подряд.
    """
    if len(values) < 5:
        return None
    now, past = values[-1], values[-5]
    if now is None or past is None or past == 0:
        return None
    return (now - past) / abs(past)


def evaluate(periods: list[DemandPeriod]) -> DemandResult:
    """Оценить ускорение конечного спроса."""
    result = DemandResult(
        strategy_key=STRATEGY_KEY, strategy_version=STRATEGY_VERSION
    )

    if len(periods) < 5:
        result.applicable = False
        result.reason_codes.append("demand_insufficient_history")
        result.detail = (
            f"периодов {len(periods)}: сравнить с тем же периодом годом ранее "
            "не с чем"
        )
        return result

    last = periods[-1]

    search_growth = _series_growth([p.search_volume for p in periods])
    nominal = _series_growth([p.nominal_spend for p in periods])
    real_spend = real_growth(nominal, last.category_inflation)
    volume_growth = _series_growth([p.physical_volume for p in periods])

    # Ряды для устойчивого счёта: те же величины по всем периодам.
    search_series = [
        g
        for g in (
            _series_growth([p.search_volume for p in periods[: i + 1]])
            for i in range(4, len(periods))
        )
        if g is not None
    ]
    volume_series = [
        g
        for g in (
            _series_growth([p.physical_volume for p in periods[: i + 1]])
            for i in range(4, len(periods))
        )
        if g is not None
    ]

    def scored(key: str, raw: Decimal | None, series: list[Decimal], note: str):
        if raw is None:
            return Component(key, WEIGHTS[key], None, note)
        z = robust_z(series, raw) if len(series) >= 4 else None
        return Component(key, WEIGHTS[key], sigmoid(z if z is not None else raw), note)

    result.components = [
        scored("search_acceleration", search_growth, search_series,
               "поисковый интерес ускоряется"),
        Component(
            "real_spend_growth",
            WEIGHTS["real_spend_growth"],
            None if real_spend is None else sigmoid(real_spend),
            "траты растут быстрее инфляции",
        ),
        scored("physical_volume", volume_growth, volume_series,
               "физический объём растёт"),
    ]

    # ── Согласованность ──────────────────────────────────────────────────
    #
    # Три показателя, разошедшиеся в разные стороны, — это не «в среднем
    # рост», а признак того, что мы измеряем разные вещи.
    measured = [c.value for c in result.components if c.measured]
    if len(measured) >= 2:
        spread = pstdev(float(v) for v in measured)
        result.alignment = q(Decimal(str(1 - min(spread / 0.5, 1.0))))
    else:
        result.alignment = Decimal(1)

    raw = [
        ("search_acceleration", search_growth),
        ("real_spend_growth", real_spend),
        ("physical_volume", volume_growth),
    ]
    positive = [v for _, v in raw if v is not None and v > 0]
    negative = [v for _, v in raw if v is not None and v < 0]
    result.aligned = max(len(positive), len(negative))

    result.score()
    result.strength = q(result.strength * result.alignment)

    # ── Антишумовые правила §13.2 ────────────────────────────────────────
    if last.ad_campaign and volume_growth is None and (real_spend is None or real_spend <= 0):
        result.reason_codes.append("demand_promotion_only")
        result.direction = "neutral"
        result.detail = (
            "рекламная кампания подняла запросы, но ни трат, ни объёма это "
            "не подтверждает"
        )
        return _finish(result, volume_growth)
    if last.news_spike:
        result.reason_codes.append("demand_informational_intent")
    if last.out_of_stock and volume_growth is not None and volume_growth < 0:
        # Спрос есть, выручки нет: товара не было. Это не рост продаж.
        result.reason_codes.append("demand_unmet_out_of_stock")
        result.direction = "neutral"
        result.detail = "спрос выше предложения: продать было нечего"
        return _finish(result, volume_growth)
    if (
        volume_growth is not None
        and volume_growth > 0
        and last.margin_change is not None
        and last.margin_change < 0
    ):
        result.reason_codes.append("demand_promotion_driven")

    if nominal is not None and real_spend is not None and nominal > 0 >= real_spend:
        # Номинальный рост ниже инфляции — это падение, а не рост.
        result.reason_codes.append("demand_below_inflation")
        result.direction = "negative"
        result.detail = (
            f"номинальный рост {nominal:.1%} ниже инфляции: реальные траты "
            f"падают на {abs(real_spend):.1%}"
        )
        return _finish(result, volume_growth)

    if result.aligned >= MIN_ALIGNED and positive and len(positive) >= len(negative):
        result.direction = "positive"
        result.detail = "спрос ускоряется по нескольким независимым измерениям"
    elif result.aligned >= MIN_ALIGNED and negative:
        result.direction = "negative"
        result.detail = "спрос замедляется по нескольким измерениям"
    else:
        result.direction = "neutral"
        result.detail = "компоненты спроса не согласованы между собой"

    return _finish(result, volume_growth)


def _finish(result: DemandResult, volume_growth: Decimal | None) -> DemandResult:
    """Ограничить оценку, если физического объёма нет.

    Рост запросов и трат без штук — гипотеза о внимании, а не о спросе.
    Потолок здесь не наказание, а граница того, что этими данными можно
    утверждать.
    """
    if volume_growth is None:
        result.economic_cap = NO_VOLUME_CAP
        result.reason_codes.append("demand_no_physical_volume")
        result.cap_confidence(NO_VOLUME_CAP, "demand_no_physical_volume")
    return result


__all__ = [
    "DemandPeriod",
    "DemandResult",
    "MIN_ALIGNED",
    "NO_VOLUME_CAP",
    "STRATEGY_KEY",
    "STRATEGY_VERSION",
    "WEIGHTS",
    "evaluate",
]
