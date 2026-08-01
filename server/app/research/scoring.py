"""Оценки сигнала и правило 3–2–1 (ТЗ Early Signals §12).

Здесь считается всё, что отделяет проверяемое утверждение от красивого.
Четыре величины измеряются раздельно и раздельно же показываются — §2.6
запрещает сводить их в один балл без расшифровки, и запрет не декоративный:
сильное экономическое изменение при слабых доказательствах и слабое при
безупречных — это разные ситуации, требующие разных действий, а средним
баллом они неотличимы.

Про независимость источников. Три новости об одном пресс-релизе — это один
источник (§2.4). Считается по ``lineage_root_id``, а не по домену и не по
названию агентства: пересказ не добавляет знания, сколько бы раз его ни
повторили. Это главная защита от иллюзии подтверждения.

Про рыночный контекст. Он может отсутствовать — лицензированных данных о
консенсусе и позиционировании у нас нет. Отсутствие остаётся отсутствием:
``None``, а не 0.5. Подстановка среднего превратила бы «мы не знаем, учтено
ли это в цене» в «наполовину учтено», то есть выдумала бы знание.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from ..models.enums import EvidenceRole, ResearchDirection

# ── Версии формул ───────────────────────────────────────────────────────────
#
# Версия обязательна: веса — это конфигурация, а не истина, и результат,
# посчитанный вчерашними весами, обязан быть отличим от сегодняшнего.
EVIDENCE_SCORE_VERSION = "evidence_score_v1"
ECONOMIC_SCORE_VERSION = "economic_score_v1"
PRIORITY_VERSION = "research_priority_v1"

# Веса §12.2. Сумма единица — проверяется тестом, а не на глаз.
EVIDENCE_WEIGHTS = {
    "source_quality": 0.25,
    "independence": 0.25,
    "persistence": 0.20,
    "freshness": 0.15,
    "data_quality": 0.15,
}

# Веса §12.3.
ECONOMIC_WEIGHTS = {
    "effect_size": 0.40,
    "exposure_confidence": 0.30,
    "causal_path_quality": 0.30,
}

# Веса §12.5.
PRIORITY_WEIGHTS = {"economic": 0.45, "evidence": 0.35, "market": 0.20}

# Пороги hard gate §12.6.
MIN_LINEAGE_ROOTS = 3
MIN_DATA_TYPES = 2
MIN_CONFIRMATIONS = 2
MIN_EVIDENCE_SCORE = Decimal("0.65")
MIN_ECONOMIC_SCORE = Decimal("0.60")
MIN_ENTITY_CONFIDENCE = Decimal("0.90")

# Доли противоречий §14.3.
CONTRADICTION_BLOCK = Decimal("0.50")
CONTRADICTION_REVIEW = Decimal("0.25")


def _q(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """Одно доказательство при гипотезе."""

    lineage_root_id: str
    data_type: str
    role: EvidenceRole
    source_quality: float
    freshness: float
    data_quality: float
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class EvidenceScore:
    value: Decimal
    components: dict[str, Decimal]
    unique_lineage_roots: int
    data_types: int
    version: str = EVIDENCE_SCORE_VERSION


def evidence_score(
    items: list[EvidenceItem], *, consecutive_confirmations: int
) -> EvidenceScore:
    """Надёжность доказательной базы (§12.2).

    Учитываются только поддерживающие и опровергающие доказательства.
    Контекст в независимость не входит: он объясняет обстановку, но ничего
    не подтверждает, и засчитывать его за источник значило бы набирать
    тройку из фона.
    """
    counting = [i for i in items if i.role in (EvidenceRole.SUPPORT, EvidenceRole.CONTRADICT)]
    if not counting:
        zero = {name: Decimal(0) for name in EVIDENCE_WEIGHTS}
        return EvidenceScore(Decimal(0), zero, 0, 0)

    total_weight = sum(i.weight for i in counting) or 1.0
    roots = {i.lineage_root_id for i in counting}
    types = {i.data_type for i in counting if i.data_type}

    quality = sum(i.source_quality * i.weight for i in counting) / total_weight
    independence = _clamp(len(roots) / MIN_LINEAGE_ROOTS)
    persistence = _clamp(consecutive_confirmations / MIN_CONFIRMATIONS)
    # Свежесть — по самому старому ключевому доказательству, а не по среднему:
    # цепочка не свежее своего самого несвежего звена.
    fresh = min(i.freshness for i in counting)
    data_quality = sum(i.data_quality * i.weight for i in counting) / total_weight

    parts = {
        "source_quality": quality,
        "independence": independence,
        "persistence": persistence,
        "freshness": fresh,
        "data_quality": data_quality,
    }
    total = sum(EVIDENCE_WEIGHTS[name] * _clamp(v) for name, v in parts.items())
    return EvidenceScore(
        value=_q(total),
        components={name: _q(_clamp(v)) for name, v in parts.items()},
        unique_lineage_roots=len(roots),
        data_types=len(types),
    )


@dataclass(frozen=True, slots=True)
class EconomicScore:
    value: Decimal
    components: dict[str, Decimal]
    version: str = ECONOMIC_SCORE_VERSION


def economic_score(
    *,
    effect_size: float,
    exposure_confidence: float,
    causal_path_quality: float,
) -> EconomicScore:
    """Материальность изменения для конкретного эмитента (§12.3).

    ``causal_path_quality = 0`` при отсутствии целевого KPI или ожидаемого
    лага — и это не штраф, а определение: утверждение без проверяемого
    следствия и без срока не является гипотезой, его нечем опровергнуть.
    """
    parts = {
        "effect_size": _clamp(effect_size),
        "exposure_confidence": _clamp(exposure_confidence),
        "causal_path_quality": _clamp(causal_path_quality),
    }
    total = sum(ECONOMIC_WEIGHTS[name] * v for name, v in parts.items())
    return EconomicScore(
        value=_q(total),
        components={name: _q(v) for name, v in parts.items()},
    )


@dataclass(frozen=True, slots=True)
class Priority:
    value: Decimal
    market_context_missing: bool
    weights: dict[str, float]
    version: str = PRIORITY_VERSION


def research_priority(
    *,
    economic: Decimal,
    evidence: Decimal,
    market: Decimal | None,
    risk_penalty: Decimal = Decimal(0),
) -> Priority:
    """Приоритет проверки — не прогноз доходности (§12.5).

    Отсутствующий рыночный контекст не заменяется средним. Веса
    перенормируются между двумя оставшимися величинами, а результат несёт
    флаг: гипотеза без рыночного контекста не может стать готовой к
    углублённому анализу автоматически.
    """
    missing = market is None
    if missing:
        share = PRIORITY_WEIGHTS["economic"] + PRIORITY_WEIGHTS["evidence"]
        weights = {
            "economic": PRIORITY_WEIGHTS["economic"] / share,
            "evidence": PRIORITY_WEIGHTS["evidence"] / share,
            "market": 0.0,
        }
        base = (
            Decimal(str(weights["economic"])) * economic
            + Decimal(str(weights["evidence"])) * evidence
        )
    else:
        weights = dict(PRIORITY_WEIGHTS)
        base = (
            Decimal(str(weights["economic"])) * economic
            + Decimal(str(weights["evidence"])) * evidence
            + Decimal(str(weights["market"])) * market
        )
    value = Decimal(100) * base * (Decimal(1) - risk_penalty)
    return Priority(
        value=value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP),
        market_context_missing=missing,
        weights=weights,
    )


def contradiction_ratio(items: list[EvidenceItem]) -> Decimal:
    """Доля опровергающих доказательств по весу (§14.3).

    Противоречие не удаляется и не «перевешивается» поддержкой: оно
    считается и влияет на то, можно ли подтвердить гипотезу автоматически.
    """
    counting = [i for i in items if i.role in (EvidenceRole.SUPPORT, EvidenceRole.CONTRADICT)]
    total = sum(i.weight for i in counting)
    if total <= 0:
        return Decimal(0)
    against = sum(i.weight for i in counting if i.role is EvidenceRole.CONTRADICT)
    return _q(against / total)


@dataclass(frozen=True, slots=True)
class GateResult:
    """Итог правила 3–2–1: прошла или нет и чего именно не хватило."""

    passed: bool
    failures: tuple[str, ...] = ()
    facts: dict = field(default_factory=dict)

    @property
    def reason(self) -> str:
        return "; ".join(self.failures) if self.failures else "все условия §12.6 выполнены"


def three_two_one(
    *,
    evidence: EvidenceScore,
    economic: EconomicScore,
    confirmations: int,
    causal_path_valid: bool,
    entity_confidence: Decimal,
    contradictions: Decimal,
    critical_dq: bool = False,
) -> GateResult:
    """Жёсткие ворота подтверждения гипотезы (§12.6).

    Все семь условий обязательны одновременно. Каждое отсекает свой способ
    ошибиться, и ни одно не компенсируется другим: безупречная причинная
    цепочка на одном источнике — это по-прежнему один источник.
    """
    failures: list[str] = []
    if evidence.unique_lineage_roots < MIN_LINEAGE_ROOTS:
        failures.append(
            f"независимых источников {evidence.unique_lineage_roots} "
            f"из {MIN_LINEAGE_ROOTS}"
        )
    if evidence.data_types < MIN_DATA_TYPES:
        failures.append(
            f"типов данных {evidence.data_types} из {MIN_DATA_TYPES}: "
            "подтверждение одним способом измерения не считается независимым"
        )
    if confirmations < MIN_CONFIRMATIONS:
        failures.append(
            f"последовательных подтверждений {confirmations} из {MIN_CONFIRMATIONS}"
        )
    if not causal_path_valid:
        failures.append("причинная цепочка до конкретного KPI не построена")
    if entity_confidence < MIN_ENTITY_CONFIDENCE:
        failures.append(
            f"уверенность в сущности {entity_confidence} ниже {MIN_ENTITY_CONFIDENCE}"
        )
    if critical_dq:
        failures.append("открыт критический инцидент качества данных")
    if contradictions >= CONTRADICTION_BLOCK:
        failures.append(
            f"доля опровергающих доказательств {contradictions} — "
            "автоматическое подтверждение запрещено"
        )
    if evidence.value < MIN_EVIDENCE_SCORE:
        failures.append(f"надёжность {evidence.value} ниже {MIN_EVIDENCE_SCORE}")
    if economic.value < MIN_ECONOMIC_SCORE:
        failures.append(f"материальность {economic.value} ниже {MIN_ECONOMIC_SCORE}")

    return GateResult(
        passed=not failures,
        failures=tuple(failures),
        facts={
            "unique_lineage_roots": evidence.unique_lineage_roots,
            "independence_groups": evidence.data_types,
            "confirmation_periods": confirmations,
            "causal_path_valid": causal_path_valid,
            "contradiction_ratio": str(contradictions),
        },
    )


def signed(direction: ResearchDirection, strength: float) -> Decimal:
    """Сила со знаком направления (§12.1)."""
    magnitude = Decimal(str(_clamp(strength)))
    if direction is ResearchDirection.NEGATIVE:
        return -magnitude
    if direction is ResearchDirection.NEUTRAL:
        return Decimal(0)
    return magnitude


__all__ = [
    "CONTRADICTION_BLOCK",
    "CONTRADICTION_REVIEW",
    "ECONOMIC_WEIGHTS",
    "EVIDENCE_WEIGHTS",
    "EconomicScore",
    "EvidenceItem",
    "EvidenceScore",
    "GateResult",
    "MIN_ECONOMIC_SCORE",
    "MIN_EVIDENCE_SCORE",
    "PRIORITY_WEIGHTS",
    "Priority",
    "contradiction_ratio",
    "economic_score",
    "evidence_score",
    "research_priority",
    "signed",
    "three_two_one",
]
