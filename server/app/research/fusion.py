"""Объединение сигналов в гипотезу и её жизненный цикл (§12.7, §14).

Один сигнал — это наблюдение, а не гипотеза. Гипотеза появляется, когда
несколько независимых наблюдений складываются в **причинную цепочку**:
что-то изменилось → это влияет на операционный драйвер → драйвер меняет
конкретный финансовый показатель → показатель относится к этому эмитенту.
Без такой цепочки утверждение нечем опровергнуть, а значит нечем и
проверить.

Три правила, которые здесь важнее остального.

**Объединять по экономике, а не по тикеру** (§14.1). Два сигнала об одной
бумаге могут говорить о разном: рост найма в ИТ-подразделении и падение
цен на сырьё — это две разные истории, и склеивать их в одну «гипотезу по
компании» значит получить утверждение, которое нельзя ни подтвердить, ни
опровергнуть целиком.

**Противоречие не удаляется** (§14.3). Доля опровергающих доказательств
считается и влияет на решение. Гипотеза, из которой вычистили неудобные
факты, перестаёт быть проверяемой — она становится позицией.

**Новое доказательство поднимает версию, а не плодит копии** (§14.5).
Отпечаток замысла считается из сущности, направления, семейства KPI,
драйвера и корзины лага. Две записи об одном и том же — это не два сигнала,
это потерянная история изменения.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ..models.enums import EvidenceRole, HypothesisState, ResearchDirection
from .investability import GateResult as Investability
from .investability import GateStatus
from .scoring import (
    CONTRADICTION_BLOCK,
    CONTRADICTION_REVIEW,
    EvidenceItem,
    EvidenceScore,
    GateResult,
    contradiction_ratio,
    economic_score,
    evidence_score,
    research_priority,
    three_two_one,
)

# Насколько должны пересекаться окна эффекта, чтобы сигналы считались об
# одном и том же (§14.1). Тридцать процентов — граница между «об одном
# событии» и «о двух разных, случившихся рядом».
MIN_WINDOW_OVERLAP = 0.30

# Корзины лага для отпечатка. Гипотеза с эффектом «через квартал» и
# «через два года» — разные гипотезы, даже если всё остальное совпало.
LAG_BUCKETS = ((90, "P1Q"), (180, "P2Q"), (365, "P1Y"), (730, "P2Y"))


@dataclass(frozen=True, slots=True)
class SignalInput:
    """Один сигнал движка на входе объединения."""

    strategy_key: str
    entity_id: str
    instrument_id: str
    direction: ResearchDirection
    strength: Decimal
    target_kpi_family: str
    causal_driver: str
    # Окно проявления эффекта в днях от as_of.
    window_from_days: int
    window_to_days: int
    evidence: tuple[EvidenceItem, ...] = ()
    reason_codes: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Falsifier:
    """Что именно опровергнет гипотезу (§16.3).

    Формулировка обязана быть наблюдаемой. «Рынок ухудшится» — не
    опровержение: его нельзя проверить, а значит гипотеза бессмертна.
    """

    description: str
    metric_or_event: str
    operator: str
    threshold: float
    check_frequency: str = "P1D"
    on_trigger: str = "invalidate"

    @property
    def observable(self) -> bool:
        return bool(self.metric_or_event and self.operator)

    def as_dict(self) -> dict:
        return {
            "description": self.description,
            "metric_or_event": self.metric_or_event,
            "operator": self.operator,
            "threshold": self.threshold,
            "check_frequency": self.check_frequency,
            "on_trigger": self.on_trigger,
        }


@dataclass
class Fused:
    """Результат объединения: гипотеза со всем, что её делает проверяемой."""

    fingerprint: str
    entity_id: str
    instrument_id: str
    direction: ResearchDirection
    title: str
    state: HypothesisState
    state_reason: str
    expected_lag: str
    evidence: EvidenceScore
    economic: Decimal
    priority: Decimal
    market_context_missing: bool
    gate: GateResult
    contradictions: Decimal
    investability: Investability | None = None
    causal_path: dict = field(default_factory=dict)
    target_kpis: list[dict] = field(default_factory=list)
    falsifiers: list[dict] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    signals: tuple[str, ...] = ()
    expires_at: datetime | None = None


def _overlap(a: SignalInput, b: SignalInput) -> float:
    """Доля пересечения окон эффекта."""
    lo = max(a.window_from_days, b.window_from_days)
    hi = min(a.window_to_days, b.window_to_days)
    if hi <= lo:
        return 0.0
    shortest = min(
        a.window_to_days - a.window_from_days, b.window_to_days - b.window_from_days
    )
    if shortest <= 0:
        return 0.0
    return (hi - lo) / shortest


def groupable(a: SignalInput, b: SignalInput) -> bool:
    """Об одном ли экономическом событии эти два сигнала (§14.1).

    Совпадения тикера недостаточно и никогда не будет достаточно: рост
    найма и падение сырья — разные истории про одну компанию.
    """
    if a.entity_id != b.entity_id:
        return False
    if a.target_kpi_family != b.target_kpi_family:
        return False
    if a.direction is not b.direction:
        return False
    return _overlap(a, b) >= MIN_WINDOW_OVERLAP


def group(signals: list[SignalInput]) -> list[list[SignalInput]]:
    """Разложить сигналы по экономическим историям."""
    buckets: list[list[SignalInput]] = []
    for signal in signals:
        for bucket in buckets:
            if all(groupable(signal, other) for other in bucket):
                bucket.append(signal)
                break
        else:
            buckets.append([signal])
    return buckets


def _lag_bucket(days: int) -> str:
    for limit, name in LAG_BUCKETS:
        if days <= limit:
            return name
    return "P2Y+"


def fingerprint(signals: list[SignalInput]) -> str:
    """Отпечаток замысла (§14.5).

    Сущность, направление, семейство KPI, драйвер и корзина лага. Драйверы
    сортируются: порядок сигналов на входе не должен менять личность
    гипотезы.
    """
    head = signals[0]
    drivers = ",".join(sorted({s.causal_driver for s in signals}))
    lag = _lag_bucket(max(s.window_to_days for s in signals))
    raw = "|".join(
        [head.entity_id, str(head.direction), head.target_kpi_family, drivers, lag]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def causal_path(signals: list[SignalInput]) -> dict:
    """Цепочка от наблюдения до бумаги (§14.2).

    Валидная цепочка обязана дойти до конкретного показателя и до
    инструмента. Оборванная — это утверждение «что-то стало лучше», из
    которого не следует ничего проверяемого.
    """
    head = signals[0]
    nodes = [
        {"id": f"s{i}", "type": "signal", "name": s.strategy_key}
        for i, s in enumerate(signals)
    ]
    nodes.append({"id": "driver", "type": "operational_driver",
                  "name": head.causal_driver})
    nodes.append({"id": "kpi", "type": "financial_kpi",
                  "name": head.target_kpi_family})
    if head.instrument_id:
        nodes.append({"id": "security", "type": "security",
                      "ref_id": head.instrument_id})
    edges = [
        {"from": f"s{i}", "to": "driver", "relation": "supports"}
        for i in range(len(signals))
    ]
    edges.append({
        "from": "driver",
        "to": "kpi",
        "relation": "expected_to_affect",
        "lag": _lag_bucket(max(s.window_to_days for s in signals)),
    })
    if head.instrument_id:
        edges.append({"from": "kpi", "to": "security",
                      "relation": "economic_exposure"})
    return {"nodes": nodes, "edges": edges}


def path_valid(path: dict, *, driver: str, kpi: str) -> bool:
    """Дошла ли цепочка до показателя и до бумаги."""
    kinds = {n.get("type") for n in path.get("nodes", ())}
    if not driver or not kpi:
        return False
    return {"operational_driver", "financial_kpi", "security"} <= kinds


def fuse(
    signals: list[SignalInput],
    *,
    confirmations: int,
    entity_confidence: Decimal,
    effect_size: float,
    exposure_confidence: float,
    falsifiers: list[Falsifier],
    market_context: Decimal | None = None,
    investability: Investability | None = None,
    critical_dq: bool = False,
    now: datetime | None = None,
    horizon_days: int = 730,
) -> Fused:
    """Собрать гипотезу из группы сигналов и определить её состояние.

    Состояние не назначается, а выводится: наблюдение без причинной цепочки,
    ранний кандидат с цепочкой, подтверждённая — только после правила
    3–2–1. Подняться выше по одному сильному сигналу нельзя ни при какой
    силе сигнала.
    """
    moment = now or datetime.now(UTC)
    head = signals[0]
    evidence_items = [item for s in signals for item in s.evidence]

    path = causal_path(signals)
    valid_path = path_valid(
        path, driver=head.causal_driver, kpi=head.target_kpi_family
    )
    # Опровержения обязаны быть наблюдаемыми. Ненаблюдаемое опровержение
    # хуже отсутствующего: оно создаёт видимость проверяемости.
    usable_falsifiers = [f for f in falsifiers if f.observable]

    e_score = evidence_score(evidence_items, consecutive_confirmations=confirmations)
    k_score = economic_score(
        effect_size=effect_size,
        exposure_confidence=exposure_confidence,
        # Цепочка без опровержений не считается полноценной: §12.3 требует
        # целевого KPI и лага, §16.3 — проверяемого условия отказа.
        causal_path_quality=0.9 if valid_path and usable_falsifiers else 0.0,
    )
    contradictions = contradiction_ratio(evidence_items)
    gate = three_two_one(
        evidence=e_score,
        economic=k_score,
        confirmations=confirmations,
        causal_path_valid=valid_path,
        entity_confidence=entity_confidence,
        contradictions=contradictions,
        critical_dq=critical_dq,
    )
    priority = research_priority(
        economic=k_score.value, evidence=e_score.value, market=market_context
    )

    state, reason = _state(
        gate=gate,
        valid_path=valid_path,
        falsifiers=usable_falsifiers,
        contradictions=contradictions,
        market_missing=priority.market_context_missing,
        investability=investability,
    )

    missing: list[str] = []
    if not usable_falsifiers:
        missing.append("нет наблюдаемого условия опровержения")
    if investability is not None:
        missing.extend(investability.missing_required_data)
    if priority.market_context_missing:
        missing.append("нет данных о том, учтено ли изменение в цене")
    missing.extend(gate.failures)

    lag = _lag_bucket(max(s.window_to_days for s in signals))
    return Fused(
        fingerprint=fingerprint(signals),
        entity_id=head.entity_id,
        instrument_id=head.instrument_id,
        direction=head.direction,
        title=_title(signals),
        state=state,
        state_reason=reason,
        expected_lag=lag,
        evidence=e_score,
        economic=k_score.value,
        priority=priority.value,
        market_context_missing=priority.market_context_missing,
        gate=gate,
        contradictions=contradictions,
        investability=investability,
        causal_path=path,
        target_kpis=[
            {
                "metric": head.target_kpi_family,
                "expected_direction": (
                    "up" if head.direction is ResearchDirection.POSITIVE else "down"
                ),
                "measurement_window": {
                    "from": f"P{head.window_from_days}D",
                    "to": f"P{head.window_to_days}D",
                },
            }
        ],
        falsifiers=[f.as_dict() for f in usable_falsifiers],
        missing_evidence=missing,
        signals=tuple(s.strategy_key for s in signals),
        expires_at=moment + timedelta(days=horizon_days),
    )


def _state(
    *,
    gate: GateResult,
    valid_path: bool,
    falsifiers: list[Falsifier],
    contradictions: Decimal,
    market_missing: bool,
    investability: Investability | None = None,
) -> tuple[HypothesisState, str]:
    """Вывести состояние из фактов, а не назначить.

    Порядок ветвлений — от самого запрещающего к самому разрешающему.
    Половина опровержений блокирует автоподтверждение независимо от того,
    насколько хороши остальные числа (§14.3).
    """
    if contradictions >= CONTRADICTION_BLOCK:
        return (
            HypothesisState.OBSERVATION,
            f"опровергающих доказательств {contradictions:.0%} — "
            "автоматическое подтверждение запрещено, нужен разбор",
        )
    if not valid_path or not falsifiers:
        missing = (
            "причинная цепочка до конкретного показателя не построена"
            if not valid_path
            else "нет наблюдаемого условия, которое её опровергнет"
        )
        return HypothesisState.OBSERVATION, f"это наблюдение, а не гипотеза: {missing}"
    if not gate.passed:
        return HypothesisState.EARLY_CANDIDATE, gate.reason
    if market_missing:
        # §12.5: без рыночного контекста гипотеза не может стать готовой к
        # разбору автоматически. Подтверждённой — может: экономика доказана,
        # неизвестно лишь, знает ли о ней рынок.
        return (
            HypothesisState.CONFIRMED,
            "правило 3–2–1 выполнено; готовность к разбору не присваивается "
            "автоматически: неизвестно, учтено ли изменение в цене",
        )
    if contradictions >= CONTRADICTION_REVIEW:
        return (
            HypothesisState.CONFIRMED,
            f"правило 3–2–1 выполнено, но опровержений {contradictions:.0%} — "
            "нужен разбор перед углублённым анализом",
        )
    # Экономика доказана — теперь спрашиваем, годится ли инструмент. Это
    # разные вопросы (§2.6), и сильная находка у неликвидной бумаги не
    # исчезает: она остаётся подтверждённой гипотезой с названной причиной,
    # по которой торговать по ней нечем.
    if investability is None:
        return (
            HypothesisState.CONFIRMED,
            "правило 3–2–1 выполнено; пригодность инструмента не проверялась",
        )
    if not investability.allows_diligence:
        blockers = investability.hard_blocks or investability.missing_required_data
        return (
            HypothesisState.CONFIRMED,
            f"правило 3–2–1 выполнено, но инструмент не проходит: "
            f"{'; '.join(blockers[:2])}",
        )
    return HypothesisState.DILIGENCE_READY, "правило 3–2–1 выполнено, риски проверены"


def _title(signals: list[SignalInput]) -> str:
    head = signals[0]
    move = "улучшение" if head.direction is ResearchDirection.POSITIVE else "ухудшение"
    if head.direction is ResearchDirection.NEUTRAL:
        move = "изменение без направления"
    return (
        f"{head.causal_driver}: {move} может отразиться на "
        f"{head.target_kpi_family}"
    )[:300]


def supersedes(previous: Fused | None, current: Fused) -> bool:
    """Стоит ли записывать новую версию.

    Версия поднимается только при изменении по существу: состояния,
    приоритета или состава доказательств. Пересчёт, давший то же самое, —
    это не событие, и засорять им историю значит сделать её нечитаемой.
    """
    if previous is None:
        return True
    return (
        previous.state is not current.state
        or previous.priority != current.priority
        or previous.evidence.unique_lineage_roots
        != current.evidence.unique_lineage_roots
    )


__all__ = [
    "Falsifier",
    "Fused",
    "LAG_BUCKETS",
    "MIN_WINDOW_OVERLAP",
    "SignalInput",
    "causal_path",
    "fingerprint",
    "fuse",
    "group",
    "groupable",
    "path_valid",
    "supersedes",
]
