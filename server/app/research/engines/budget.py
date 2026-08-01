"""Движок BUDGET: фактический бюджетный импульс (ТЗ §13.6).

Между «объявили программу» и «подрядчик получил деньги» лежит длинная
лестница, и почти вся публичная реакция происходит на первой ступени. Этот
движок измеряет, докуда дело дошло на самом деле.

Ступени и веса — из §13.6, и нулевой вес первой ступени не формальность:
**публичное намерение без ассигнований не создаёт сигнала вовсе**. Пресс-
релиз о триллионной программе, под которую не расписаны лимиты, не является
доказательством ничего.

Дальше две ловушки, обе связаны с деньгами, которые выглядят больше, чем
есть.

**Номинальный рост.** Бюджет, выросший на 8% при инфляции 10%, сократился.
Считать импульс по номиналу — значит объявлять сокращение расширением.

**Декабрьский всплеск.** Кассовое исполнение в России сдвинуто к концу
года по устройству процесса, а не по решению. Всплеск в декабре — это
календарь, и сравнивать его надо с декабрём, а не с ноябрём.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .base import Component, Evaluation, clamp, q, real_growth

STRATEGY_KEY = "BUDGET"
STRATEGY_VERSION = "1.0.0"

# Лестница финансирования (§13.6).
STAGE_WEIGHTS: dict[str, Decimal] = {
    "intent": Decimal("0.00"),
    "appropriation": Decimal("0.05"),
    "limits": Decimal("0.15"),
    "cash_execution": Decimal("0.30"),
    "procurement_notice": Decimal("0.35"),
    "contract": Decimal("0.55"),
    "advance": Decimal("0.70"),
    "acceptance": Decimal("0.90"),
    "payment": Decimal("1.00"),
}

# Что требуется для подтверждённой гипотезы (§13.6): либо кассовое
# исполнение вместе с контрактом, либо контракт с авансом или приёмкой.
CONFIRM_COMBINATIONS = (
    frozenset({"cash_execution", "contract"}),
    frozenset({"contract", "advance"}),
    frozenset({"contract", "acceptance"}),
)

WEIGHTS = {
    "impulse": 0.40,
    "execution_ratio": 0.25,
    "contract_conversion": 0.20,
    "attribution": 0.15,
}

# Месяц, в котором кассовый всплеск объясняется календарём.
YEAR_END_MONTH = 12


@dataclass(frozen=True, slots=True)
class Flow:
    """Один поток бюджетных денег на своей ступени."""

    stage: str
    amount: Decimal
    attribution_confidence: Decimal = Decimal(1)
    month: int = 1
    sequestered: bool = False
    subsidy_covers_loss: bool = False
    foreign_supplier: bool = False


@dataclass
class BudgetResult(Evaluation):
    weighted_flow: Decimal = Decimal(0)
    weighted_flow_prior: Decimal = Decimal(0)
    real_impulse: Decimal | None = None
    stages_reached: frozenset[str] = frozenset()


def weighted_flow(flows: list[Flow]) -> Decimal:
    """Взвешенная сумма с учётом ступени и уверенности в привязке."""
    total = Decimal(0)
    for flow in flows:
        if flow.foreign_supplier:
            # Программа, финансирующая импорт, не создаёт выручку
            # публичного российского подрядчика.
            continue
        total += (
            flow.amount
            * STAGE_WEIGHTS.get(flow.stage, Decimal(0))
            * flow.attribution_confidence
        )
    return q(total, "0.01")


def evaluate(
    current: list[Flow],
    prior: list[Flow],
    *,
    inflation: Decimal | None = None,
    annual_appropriation: Decimal | None = None,
    announced_procurement: Decimal | None = None,
    contracted: Decimal | None = None,
    beneficiary_exposure: Decimal | None = None,
) -> BudgetResult:
    """Оценить, во что превратилось бюджетное решение."""
    result = BudgetResult(
        strategy_key=STRATEGY_KEY, strategy_version=STRATEGY_VERSION
    )

    result.weighted_flow = weighted_flow(current)
    result.weighted_flow_prior = weighted_flow(prior)
    result.stages_reached = frozenset(f.stage for f in current)

    if result.weighted_flow <= 0:
        result.applicable = False
        result.reason_codes.append("budget_intent_only")
        result.detail = (
            "есть только объявленное намерение: под него не расписано "
            "ни лимитов, ни денег"
        )
        return result

    if result.weighted_flow_prior > 0:
        nominal = (
            result.weighted_flow - result.weighted_flow_prior
        ) / result.weighted_flow_prior
    else:
        nominal = Decimal(1)
    result.real_impulse = real_growth(nominal, inflation)

    execution_ratio = (
        result.weighted_flow / annual_appropriation
        if annual_appropriation and annual_appropriation > 0
        else None
    )
    conversion = (
        contracted / announced_procurement
        if contracted is not None
        and announced_procurement
        and announced_procurement > 0
        else None
    )

    impulse_value = (
        None
        if result.real_impulse is None
        else q(Decimal(str(clamp(float(result.real_impulse) + 0.5))))
    )
    result.components = [
        Component("impulse", WEIGHTS["impulse"], impulse_value,
                  "реальный поток ускоряется"),
        Component(
            "execution_ratio",
            WEIGHTS["execution_ratio"],
            None if execution_ratio is None else q(Decimal(str(clamp(float(execution_ratio))))),
            "ассигнования доходят до кассы",
        ),
        Component(
            "contract_conversion",
            WEIGHTS["contract_conversion"],
            None if conversion is None else q(Decimal(str(clamp(float(conversion))))),
            "объявленное превращается в контракты",
        ),
        Component(
            "attribution",
            WEIGHTS["attribution"],
            None
            if beneficiary_exposure is None
            else q(Decimal(str(clamp(float(beneficiary_exposure))))),
            "доля программы в выручке бенефициара измерена",
        ),
    ]
    result.score()

    if beneficiary_exposure is None:
        # Крупный диверсифицированный эмитент может не заметить программу
        # вовсе. Без доли эффекта утверждать материальность нельзя.
        result.reason_codes.append("budget_exposure_unknown")
        result.cap_confidence(Decimal("0.50"), "budget_exposure_unknown")

    if any(f.sequestered for f in current):
        result.reason_codes.append("budget_sequester")
    if any(f.subsidy_covers_loss for f in current):
        # Субсидия компенсирует убыток, а не создаёт прибыль.
        result.reason_codes.append("budget_subsidy_covers_loss")
    if any(f.month == YEAR_END_MONTH for f in current) and not prior:
        result.reason_codes.append("budget_year_end_spike")

    if not any(combo <= result.stages_reached for combo in CONFIRM_COMBINATIONS):
        result.reason_codes.append("budget_not_executed_yet")

    if result.real_impulse is None:
        result.direction = "neutral"
        result.detail = "инфляция неизвестна: отличить рост от индексации нечем"
        result.reason_codes.append("budget_inflation_unknown")
        return result

    if result.real_impulse <= 0:
        result.direction = "negative" if result.real_impulse < 0 else "neutral"
        result.detail = (
            f"номинальный рост {nominal:.1%} не покрывает инфляцию: реальный "
            f"импульс {result.real_impulse:.1%}"
        )
        result.reason_codes.append("budget_below_inflation")
        return result

    result.direction = "positive"
    result.detail = (
        f"реальный импульс {result.real_impulse:.1%}; дошло до ступеней: "
        f"{', '.join(sorted(result.stages_reached))}"
    )
    return result


__all__ = [
    "BudgetResult",
    "CONFIRM_COMBINATIONS",
    "Flow",
    "STAGE_WEIGHTS",
    "STRATEGY_KEY",
    "STRATEGY_VERSION",
    "WEIGHTS",
    "evaluate",
    "weighted_flow",
]
