"""Движок SUPPLIER: разведка по поставщикам (ТЗ §13.5).

Публичная компания редко объявляет о расширении заранее. Зато она заключает
контракты, и контракты видны: извещение, победитель, аванс, приёмка,
оплата. Каждая ступень — разной силы доказательство того, что деньги
действительно тратятся.

Отсюда веса состояний. Объявленный тендер весит 0.10, потому что тендер
отменяют. Заключённый контракт — 0.45. Аванс — 0.65: деньги ушли. Приёмка —
0.80. Оплата — 1.00. Отмена обнуляет и создаёт **отрицательное событие**, а
не просто исчезает из суммы.

Две ошибки, каждая из которых утраивает результат на ровном месте.

**Версии контракта складываются.** У контракта бывают изменения, и каждое
приезжает отдельной записью. Сложить их — получить втрое большую сумму,
чем есть. Сумма последней версии **заменяет** предыдущую, а не прибавляется.

**Связанные поставщики считаются независимыми.** Три компании одного
владельца — это один источник, а не три. Правило 3–2–1 на таком наборе
даёт ложное подтверждение, и защита от этого — экономическая группа, а не
разные ИНН.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .base import Component, Evaluation, clamp, q, real_growth

STRATEGY_KEY = "SUPPLIER"
STRATEGY_VERSION = "1.0.0"

# Вес доказательности по состоянию контракта (§13.5).
STATE_WEIGHTS: dict[str, Decimal] = {
    "plan": Decimal("0.05"),
    "notice": Decimal("0.10"),
    "tender": Decimal("0.20"),
    "award": Decimal("0.45"),
    "advance": Decimal("0.65"),
    "partial_acceptance": Decimal("0.80"),
    "completed": Decimal("1.00"),
    "cancelled": Decimal("0.00"),
}

# Состояния, начиная с которых сигнал вообще возможен.
EARLY_STATE = "award"
CONFIRM_STATES = frozenset({"advance", "partial_acceptance", "completed"})

WEIGHTS = {
    "acceleration": 0.45,
    "breadth": 0.30,
    "execution_quality": 0.25,
}

# Сколько независимых групп поставщиков нужно для полноты (§13.5).
FULL_BREADTH = 3


@dataclass(frozen=True, slots=True)
class Contract:
    """Контракт в канонической форме.

    ``lineage_id`` объединяет версии одного контракта. ``version`` растёт;
    в расчёт идёт только последняя.
    """

    lineage_id: str
    version: int
    state: str
    amount: Decimal
    supplier_entity_id: str
    supplier_group_id: str
    issuer_attribution: Decimal = Decimal(1)
    period: str = ""
    # Рамочная максимальная цена: это не фактическая выборка.
    framework: bool = False
    # Закупка на замену, а не на рост.
    replacement: bool = False
    intra_group: bool = False
    strong_id: bool = True


@dataclass
class SupplierResult(Evaluation):
    executed_value: Decimal = Decimal(0)
    executed_value_prior: Decimal = Decimal(0)
    breadth: int = 0
    cancelled: tuple[str, ...] = ()


def latest_versions(contracts: list[Contract]) -> list[Contract]:
    """Оставить последнюю версию каждого контракта.

    Три версии одного контракта не утраивают сумму — они её уточняют.
    """
    best: dict[str, Contract] = {}
    for contract in contracts:
        known = best.get(contract.lineage_id)
        if known is None or contract.version > known.version:
            best[contract.lineage_id] = contract
    return list(best.values())


def executed_value(
    contracts: list[Contract], *, inflation: Decimal | None = None
) -> Decimal:
    """Взвешенная сумма фактически исполняемого.

    Сумма дефлируется: рост на величину инфляции — это не рост заказов.
    """
    total = Decimal(0)
    for contract in latest_versions(contracts):
        if contract.intra_group or contract.replacement:
            continue
        weight = STATE_WEIGHTS.get(contract.state, Decimal(0))
        amount = contract.amount
        if contract.framework:
            # Рамочный договор — это потолок, а не выборка. Считать его
            # целиком значит записать в заказы то, чего может не случиться.
            amount *= Decimal("0.25")
        total += amount * weight * contract.issuer_attribution
    if inflation is not None and inflation > -1:
        total = total / (Decimal(1) + inflation)
    return q(total, "0.01")


def independence_groups(contracts: list[Contract]) -> set[str]:
    """Экономические группы поставщиков, а не юридические лица.

    Три компании одного владельца — один источник. Разные ИНН этого не
    меняют.
    """
    return {
        c.supplier_group_id or c.supplier_entity_id
        for c in latest_versions(contracts)
        if not c.intra_group and c.state != "cancelled"
    }


def evaluate(
    current: list[Contract],
    prior: list[Contract],
    *,
    inflation: Decimal | None = None,
    customer_source_confirmed: bool = False,
) -> SupplierResult:
    """Оценить ускорение заказов публичного эмитента."""
    result = SupplierResult(
        strategy_key=STRATEGY_KEY, strategy_version=STRATEGY_VERSION
    )

    weak = [c for c in latest_versions(current) if not c.strong_id]
    if weak:
        # §13.5: связь по ИНН/ОГРН обязательна. Совпадение названий —
        # это не идентификация, а догадка, и в подтверждённый сигнал она
        # не попадает.
        result.applicable = False
        result.reason_codes.append("supplier_name_only_match")
        result.detail = (
            "стороны контракта не сопоставлены сильным идентификатором"
        )
        return result

    result.executed_value = executed_value(current, inflation=inflation)
    result.executed_value_prior = executed_value(prior, inflation=inflation)
    groups = independence_groups(current)
    result.breadth = len(groups)

    cancelled = tuple(
        sorted(
            c.lineage_id
            for c in latest_versions(current)
            if c.state == "cancelled"
        )
    )
    result.cancelled = cancelled
    if cancelled:
        # Отмена — не исчезновение, а противоречие: оно пересчитывает
        # гипотезу с новой версией, а не тихо уменьшает сумму.
        result.reason_codes.append("supplier_contract_cancelled")

    if result.executed_value_prior <= 0:
        acceleration = Decimal(1) if result.executed_value > 0 else Decimal(0)
    else:
        acceleration = (
            result.executed_value - result.executed_value_prior
        ) / result.executed_value_prior

    states = {c.state for c in latest_versions(current)}
    quality = max(
        (STATE_WEIGHTS.get(s, Decimal(0)) for s in states), default=Decimal(0)
    )

    result.components = [
        Component(
            "acceleration",
            WEIGHTS["acceleration"],
            q(Decimal(str(clamp(float(acceleration) / 0.5 * 0.5 + 0.5)))),
            "исполняемые заказы ускоряются",
        ),
        Component(
            "breadth",
            WEIGHTS["breadth"],
            q(Decimal(str(clamp(result.breadth / FULL_BREADTH)))),
            "подтверждают несколько независимых поставщиков",
        ),
        Component(
            "execution_quality",
            WEIGHTS["execution_quality"],
            quality,
            "контракты дошли до денег, а не до объявления",
        ),
    ]
    result.score()

    if quality < STATE_WEIGHTS[EARLY_STATE]:
        result.reason_codes.append("supplier_notice_only")
        result.direction = "neutral"
        result.detail = (
            "есть только извещения: тендер отменяют, и заказом это ещё не стало"
        )
        return result

    enough_breadth = result.breadth >= FULL_BREADTH or (
        result.breadth >= 2 and customer_source_confirmed
    )
    if not enough_breadth:
        result.reason_codes.append("supplier_narrow_confirmation")
    if not (states & CONFIRM_STATES):
        result.reason_codes.append("supplier_no_execution_yet")

    result.direction = "positive" if acceleration > 0 else (
        "negative" if acceleration < 0 else "neutral"
    )
    result.detail = (
        f"исполняемые заказы {result.executed_value:,.0f} против "
        f"{result.executed_value_prior:,.0f}; независимых групп "
        f"{result.breadth}"
    )
    return result


__all__ = [
    "CONFIRM_STATES",
    "Contract",
    "EARLY_STATE",
    "FULL_BREADTH",
    "STATE_WEIGHTS",
    "STRATEGY_KEY",
    "STRATEGY_VERSION",
    "SupplierResult",
    "WEIGHTS",
    "evaluate",
    "executed_value",
    "independence_groups",
    "latest_versions",
]
