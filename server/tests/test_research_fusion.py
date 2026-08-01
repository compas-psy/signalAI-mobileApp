"""Объединение сигналов и жизненный цикл гипотезы §12.7, §14.

Главное, что здесь проверяется: состояние **выводится** из фактов, а не
назначается. Подняться до подтверждённой гипотезы по одному сильному
сигналу нельзя ни при какой силе сигнала — и это единственная защита от
системы, которая уверенно ошибается.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.models.enums import EvidenceRole, HypothesisState, ResearchDirection
from app.research.fusion import (
    Falsifier,
    SignalInput,
    fingerprint,
    fuse,
    group,
    groupable,
)
from app.research.investability import EquityFacts, Valuation, check_equity
from app.research.scoring import EvidenceItem

D = Decimal
NOW = datetime(2026, 8, 1, tzinfo=UTC)


def ev(root: str, kind: str, role=EvidenceRole.SUPPORT, weight=1.0) -> EvidenceItem:
    return EvidenceItem(
        lineage_root_id=root,
        data_type=kind,
        role=role,
        source_quality=0.9,
        freshness=0.9,
        data_quality=0.9,
        weight=weight,
    )


def sig(
    key: str = "BUDGET",
    *,
    entity: str = "issuer-1",
    kpi: str = "segment_revenue",
    driver: str = "исполнение контрактов",
    direction: ResearchDirection = ResearchDirection.POSITIVE,
    window: tuple[int, int] = (90, 540),
    evidence: tuple[EvidenceItem, ...] = (),
) -> SignalInput:
    return SignalInput(
        strategy_key=key,
        entity_id=entity,
        instrument_id="MOEX:EQ:TEST",
        direction=direction,
        strength=D("0.7"),
        target_kpi_family=kpi,
        causal_driver=driver,
        window_from_days=window[0],
        window_to_days=window[1],
        evidence=evidence,
    )


# Бумага, к которой нет претензий: ликвидна, долг умеренный, раскрытие
# регулярное. Нужна затем, чтобы отделить вопрос «доказано ли изменение»
# от вопроса «годится ли инструмент» — §2.6 требует считать их отдельно.
GOOD_INSTRUMENT = EquityFacts(
    exposure_proven=True,
    median_turnover_rub=50_000_000,
    free_float=0.35,
    net_debt_to_ebitda=1.5,
    interest_coverage=6.0,
    disclosure_regular=True,
    valuation_state=Valuation.REASONABLE,
)

FALSIFIER = Falsifier(
    description="Исполнение контрактов снизится два месяца подряд",
    metric_or_event="executed_contract_value_30d",
    operator="declines_consecutively",
    threshold=2,
)


def strong(**overrides):
    base = dict(
        signals=[
            sig("BUDGET", evidence=(ev("eis", "state"),)),
            sig("SUPPLIER", evidence=(ev("fns", "filing"),)),
            sig("HIRING", evidence=(ev("trudvsem", "operational"),)),
        ],
        confirmations=2,
        entity_confidence=D("0.95"),
        effect_size=0.8,
        exposure_confidence=0.8,
        falsifiers=[FALSIFIER],
        market_context=D("0.5"),
        investability=check_equity(GOOD_INSTRUMENT),
        now=NOW,
    )
    base.update(overrides)
    return fuse(**base)


# ── Группировка ─────────────────────────────────────────────────────────────


def test_совпадения_тикера_для_объединения_недостаточно():
    """Рост найма и падение сырья — разные истории про одну компанию.

    Склеить их в одну «гипотезу по компании» значит получить утверждение,
    которое нельзя ни подтвердить, ни опровергнуть целиком.
    """
    hiring = sig("HIRING", kpi="segment_revenue")
    spread = sig("SPREAD", kpi="gross_margin")
    assert not groupable(hiring, spread)


def test_разное_направление_не_объединяется():
    up = sig(direction=ResearchDirection.POSITIVE)
    down = sig(direction=ResearchDirection.NEGATIVE)
    assert not groupable(up, down)


def test_непересекающиеся_окна_эффекта_это_разные_истории():
    near = sig(window=(30, 120))
    far = sig(window=(400, 700))
    assert not groupable(near, far)


def test_сигналы_об_одном_событии_собираются_вместе():
    buckets = group([sig("BUDGET"), sig("SUPPLIER"), sig("SPREAD", kpi="gross_margin")])
    assert len(buckets) == 2
    assert len(buckets[0]) == 2


def test_отпечаток_не_зависит_от_порядка_сигналов():
    """Порядок на входе не должен менять личность гипотезы."""
    a = [sig("BUDGET"), sig("SUPPLIER")]
    b = [sig("SUPPLIER"), sig("BUDGET")]
    assert fingerprint(a) == fingerprint(b)


def test_разный_лаг_даёт_разные_гипотезы():
    """«Через квартал» и «через два года» — разные утверждения."""
    near = fingerprint([sig(window=(30, 90))])
    far = fingerprint([sig(window=(400, 700))])
    assert near != far


# ── Жизненный цикл ──────────────────────────────────────────────────────────


def test_один_сильный_сигнал_не_становится_подтверждённой_гипотезой():
    """Единственная защита от системы, которая уверенно ошибается."""
    result = strong(signals=[sig("BUDGET", evidence=(ev("eis", "state"),))])
    assert result.state is HypothesisState.EARLY_CANDIDATE
    assert "независимых источников" in result.state_reason


def test_без_причинной_цепочки_это_наблюдение():
    """Утверждение без проверяемого следствия не является гипотезой."""
    result = strong(
        signals=[
            SignalInput(
                strategy_key="DEMAND",
                entity_id="issuer-1",
                instrument_id="",  # цепочка не доходит до бумаги
                direction=ResearchDirection.POSITIVE,
                strength=D("0.7"),
                target_kpi_family="segment_revenue",
                causal_driver="поисковый интерес",
                window_from_days=90,
                window_to_days=540,
                evidence=(ev("wordstat", "search"),),
            )
        ]
    )
    assert result.state is HypothesisState.OBSERVATION
    assert "наблюдение, а не гипотеза" in result.state_reason


def test_без_наблюдаемого_опровержения_это_наблюдение():
    """Ненаблюдаемое опровержение хуже отсутствующего.

    Оно создаёт видимость проверяемости: гипотеза с условием «рынок
    ухудшится» бессмертна.
    """
    vague = Falsifier(
        description="Рынок ухудшится",
        metric_or_event="",
        operator="",
        threshold=0,
    )
    result = strong(falsifiers=[vague])
    assert result.state is HypothesisState.OBSERVATION
    assert "опровергнет" in result.state_reason
    assert result.falsifiers == []


def test_полный_набор_доводит_до_готовности_к_разбору():
    result = strong()
    assert result.state is HypothesisState.DILIGENCE_READY
    assert result.gate.passed


def test_без_рыночного_контекста_готовность_не_присваивается():
    """§12.5: экономика доказана, но неизвестно, знает ли о ней рынок."""
    result = strong(market_context=None)
    assert result.state is HypothesisState.CONFIRMED
    assert result.market_context_missing
    assert "учтено ли изменение в цене" in result.state_reason


def test_половина_опровержений_блокирует_подтверждение():
    """Независимо от того, насколько хороши остальные числа."""
    result = strong(
        signals=[
            sig("BUDGET", evidence=(ev("eis", "state"),)),
            sig("SUPPLIER", evidence=(ev("fns", "filing"),)),
            sig(
                "HIRING",
                evidence=(
                    ev("trudvsem", "operational", role=EvidenceRole.CONTRADICT,
                       weight=2.0),
                ),
            ),
        ]
    )
    assert result.state is HypothesisState.OBSERVATION
    assert "автоматическое подтверждение запрещено" in result.state_reason


def test_заметные_противоречия_требуют_разбора_но_не_отменяют():
    """Меньше половины — риск, а не запрет."""
    result = strong(
        signals=[
            sig("BUDGET", evidence=(ev("eis", "state"), ev("a", "state"))),
            sig("SUPPLIER", evidence=(ev("fns", "filing"),)),
            sig(
                "HIRING",
                evidence=(ev("trudvsem", "operational", role=EvidenceRole.CONTRADICT),),
            ),
        ]
    )
    assert result.state is HypothesisState.CONFIRMED
    assert "нужен разбор" in result.state_reason


def test_неуверенная_сущность_не_поднимает_состояние():
    """Сигнал о чужой компании хуже отсутствия сигнала."""
    result = strong(entity_confidence=D("0.7"))
    assert result.state is HypothesisState.EARLY_CANDIDATE


# ── Содержимое гипотезы ─────────────────────────────────────────────────────


def test_гипотеза_несёт_то_что_её_опровергнет():
    result = strong()
    assert result.falsifiers
    assert result.falsifiers[0]["metric_or_event"]


def test_чего_не_хватает_перечисляется_явно():
    result = strong(signals=[sig("BUDGET", evidence=(ev("eis", "state"),))])
    assert result.missing_evidence
    assert any("независимых источников" in m for m in result.missing_evidence)


def test_причинная_цепочка_доходит_до_бумаги():
    path = strong().causal_path
    kinds = {n["type"] for n in path["nodes"]}
    assert {"operational_driver", "financial_kpi", "security"} <= kinds


def test_у_гипотезы_есть_срок():
    """Без срока она живёт вечно и превращается в кладбище решений."""
    result = strong()
    assert result.expires_at is not None
    assert result.expires_at > NOW


def test_целевой_показатель_несёт_направление_и_окно():
    kpi = strong().target_kpis[0]
    assert kpi["metric"] == "segment_revenue"
    assert kpi["expected_direction"] == "up"
    assert kpi["measurement_window"]["to"] == "P540D"


# ── Пригодность инструмента ─────────────────────────────────────────────────


def test_сильный_сигнал_у_неликвидной_бумаги_не_исчезает():
    """§13.10: находка сохраняется, но торговать по ней нечем.

    Прятать её было бы хуже всего: экономический вывод верен, и владелец
    должен видеть и его, и причину, по которой инструмент не годится.
    """
    result = strong(
        investability=check_equity(
            EquityFacts(
                exposure_proven=True,
                median_turnover_rub=1_000_000,
                disclosure_regular=True,
                net_debt_to_ebitda=1.0,
            )
        )
    )
    assert result.state is HypothesisState.CONFIRMED
    assert result.gate.passed
    assert "инструмент не проходит" in result.state_reason


def test_непроверенный_инструмент_не_даёт_готовности_молча():
    result = strong(investability=None)
    assert result.state is HypothesisState.CONFIRMED
    assert "пригодность инструмента не проверялась" in result.state_reason
