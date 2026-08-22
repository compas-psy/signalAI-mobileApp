"""Допуск идеи: ACTIVE, WATCH или REJECTED (engine-ТЗ §15.6).

Порог — конъюнкция: не проходит хотя бы одно условие, и идея не ACTIVE. При
этом **причина всегда называется**. «Идея отклонена» без объяснения не
позволяет ни поспорить с движком, ни заметить, что он сломался.

«Нет сделки» — валидный результат (§32), и он оформлен как результат:
статус, список выполненных условий и список невыполненных.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..models.enums import LiquidityRegime, QualityStatus
from ..market.economic_events import EventAssessment


@dataclass(frozen=True, slots=True)
class Gate:
    name: str
    label: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Admission:
    status: QualityStatus
    gates: tuple[Gate, ...]
    reason: str

    @property
    def failed(self) -> tuple[Gate, ...]:
        return tuple(g for g in self.gates if not g.passed)


@dataclass(frozen=True, slots=True)
class AdmissionThresholds:
    """Пороги §15.6 и §27."""

    active_probability_min: Decimal = Decimal("0.56")
    active_expected_r_min: Decimal = Decimal("0.20")
    min_rr_tp2: Decimal = Decimal("2.0")
    min_confidence: Decimal = Decimal("0.50")
    watch_probability_min: Decimal = Decimal("0.50")


def admit(
    *,
    probability: Decimal,
    expected_r: Decimal,
    rr_tp2: Decimal,
    confidence: Decimal,
    liquidity: LiquidityRegime,
    has_trigger: bool,
    risk_blocked: bool,
    risk_block_reason: str = "",
    event_assessment: EventAssessment | None = None,
    thresholds: AdmissionThresholds | None = None,
) -> Admission:
    """Решить судьбу идеи по §15.6.

    Порядок проверок не случаен: сначала то, что делает сделку невозможной
    (риск-блок, непроходимая ликвидность), потом то, что делает её
    невыгодной. Отклонение по риску не должно теряться среди рассуждений о
    вероятности.
    """
    t = thresholds or AdmissionThresholds()

    gates = [
        Gate(
            "risk_block", "Риск-лимиты", not risk_blocked,
            risk_block_reason or "лимиты не превышены",
        ),
        Gate(
            "liquidity", "Ликвидность",
            liquidity in (LiquidityRegime.GOOD, LiquidityRegime.NORMAL),
            f"режим ликвидности {liquidity.value}",
        ),
        Gate(
            "rr", "Соотношение риска", rr_tp2 >= t.min_rr_tp2,
            f"R/R до TP2 {rr_tp2} против порога {t.min_rr_tp2}",
        ),
        Gate(
            "probability", "Вероятность", probability >= t.active_probability_min,
            f"p={probability} против порога {t.active_probability_min}",
        ),
        Gate(
            "expected_r", "Ожидание", expected_r >= t.active_expected_r_min,
            f"EV={expected_r}R против порога {t.active_expected_r_min}R",
        ),
        Gate(
            "confidence", "Уверенность", confidence >= t.min_confidence,
            f"confidence={confidence} против порога {t.min_confidence}",
        ),
        Gate("trigger", "Триггер", has_trigger, "триггер есть" if has_trigger else "триггера нет"),
    ]
    event_assessment = event_assessment or EventAssessment(
        "UNAVAILABLE", "EVENT_SOURCE_UNAVAILABLE", "календарь событий не передан"
    )
    gates.insert(1, Gate("economic_event", "Экономический календарь", not event_assessment.blocks_admission, event_assessment.detail))

    blocking = [
        g
        for g in gates
        if g.name in ("risk_block", "economic_event", "liquidity") and not g.passed
    ]
    if blocking:
        return Admission(
            QualityStatus.REJECTED,
            tuple(gates),
            "; ".join(g.detail for g in blocking),
        )

    failed = [g for g in gates if not g.passed]
    if not failed:
        return Admission(QualityStatus.ACTIVE, tuple(gates), "все условия §15.6 выполнены")

    # Отрицательное ожидание или слишком низкая вероятность — это отказ, а не
    # наблюдение: ждать нечего, сетап невыгоден по построению.
    if expected_r < 0 or probability < t.watch_probability_min:
        return Admission(
            QualityStatus.REJECTED,
            tuple(gates),
            "; ".join(g.detail for g in failed),
        )

    return Admission(
        QualityStatus.WATCH,
        tuple(gates),
        "не хватает: " + ", ".join(f"{g.label.lower()} ({g.detail})" for g in failed),
    )
