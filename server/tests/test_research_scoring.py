"""Две временные оси, оценки и правило 3–2–1 (§2.2, §12).

Здесь проверяется то, что нельзя увидеть на экране: не заглядывает ли
система в будущее и не набирает ли тройку независимых источников из трёх
пересказов одного пресс-релиза.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.enums import EvidenceRole, ResearchDirection
from app.research.scoring import (
    EVIDENCE_WEIGHTS,
    EvidenceItem,
    contradiction_ratio,
    economic_score,
    evidence_score,
    research_priority,
    signed,
    three_two_one,
)
from app.research.timeline import freshness, tradable_at, visible_at

D = Decimal
NOW = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)  # понедельник


def item(root: str, kind: str, role=EvidenceRole.SUPPORT, **kw) -> EvidenceItem:
    return EvidenceItem(
        lineage_root_id=root,
        data_type=kind,
        role=role,
        source_quality=kw.get("source_quality", 0.9),
        freshness=kw.get("freshness", 0.9),
        data_quality=kw.get("data_quality", 0.9),
        weight=kw.get("weight", 1.0),
    )


# ── Временные оси ───────────────────────────────────────────────────────────


def test_факт_доступен_позже_публикации_а_не_в_момент():
    """Между документом и решением проходит время на разбор.

    Тест, исходящий из мгновенной реакции, измеряет недостижимое.
    """
    a = tradable_at(published_at=NOW, first_seen_at=NOW)
    assert a.tradable_at > NOW
    assert not a.publication_time_uncertain


def test_запоздалое_обнаружение_сдвигает_доступность():
    """Публикация, которую мы увидели через сутки, эти сутки была недоступна.

    Чем бы ни был помечен документ.
    """
    seen = NOW + timedelta(days=2)
    a = tradable_at(published_at=NOW, first_seen_at=seen)
    b = tradable_at(published_at=NOW, first_seen_at=NOW)
    assert a.tradable_at > b.tradable_at
    assert "обнаружения" in a.detail


def test_отсутствие_времени_публикации_помечается():
    """Тест на таких данных систематически оптимистичен.

    Ровно на неизвестную задержку публикации — и знать об этом обязательно.
    """
    a = tradable_at(published_at=None, first_seen_at=NOW)
    assert a.publication_time_uncertain
    assert "неизвестна" in a.detail


def test_факт_после_закрытия_работает_со_следующего_дня():
    """По цене закрытия того же дня им воспользоваться было нельзя."""
    late = datetime(2026, 8, 3, 20, 0, tzinfo=UTC)
    a = tradable_at(published_at=late, first_seen_at=late)
    assert a.tradable_at.date() > late.date()


def test_доступность_не_попадает_на_выходные():
    friday_evening = datetime(2026, 8, 7, 19, 0, tzinfo=UTC)
    a = tradable_at(published_at=friday_evening, first_seen_at=friday_evening)
    assert a.tradable_at.weekday() < 5


def test_точечный_срез_не_видит_будущего():
    a = tradable_at(published_at=NOW, first_seen_at=NOW)
    assert not visible_at(a.tradable_at, NOW)
    assert visible_at(a.tradable_at, a.tradable_at)


def test_свежесть_убывает_вдвое_за_период_полураспада():
    half = timedelta(days=30)
    assert abs(freshness(timedelta(0), half) - 1.0) < 1e-9
    assert abs(freshness(half, half) - 0.5) < 1e-9
    assert abs(freshness(half * 2, half) - 0.25) < 1e-9


# ── Оценка доказательств ────────────────────────────────────────────────────


def test_веса_оценок_складываются_в_единицу():
    assert abs(sum(EVIDENCE_WEIGHTS.values()) - 1.0) < 1e-9


def test_три_пересказа_одного_релиза_это_один_источник():
    """Главная защита от иллюзии подтверждения (§2.4).

    Новость, агрегатор и пересказ несут один корень происхождения. Считать
    их за три означало бы набирать тройку независимых источников из одного.
    """
    retold = evidence_score(
        [item("release-1", "filing"), item("release-1", "filing"), item("release-1", "filing")],
        consecutive_confirmations=2,
    )
    assert retold.unique_lineage_roots == 1

    real = evidence_score(
        [item("release-1", "filing"), item("gov-stats", "state"), item("market", "market")],
        consecutive_confirmations=2,
    )
    assert real.unique_lineage_roots == 3
    assert real.value > retold.value


def test_свежесть_считается_по_самому_старому_звену():
    """Цепочка не свежее своего самого несвежего доказательства."""
    mixed = evidence_score(
        [item("a", "filing", freshness=1.0), item("b", "state", freshness=0.2)],
        consecutive_confirmations=2,
    )
    assert mixed.components["freshness"] == D("0.2000")


def test_контекст_не_засчитывается_за_источник():
    """Контекст объясняет обстановку, но ничего не подтверждает."""
    score = evidence_score(
        [
            item("a", "filing"),
            item("b", "state", role=EvidenceRole.CONTEXT),
            item("c", "market", role=EvidenceRole.CONTEXT),
        ],
        consecutive_confirmations=2,
    )
    assert score.unique_lineage_roots == 1


# ── Экономическая оценка ────────────────────────────────────────────────────


def test_без_причинной_цепочки_материальность_падает():
    """Утверждение без проверяемого следствия — не гипотеза (§12.3)."""
    strong = economic_score(effect_size=0.9, exposure_confidence=0.9, causal_path_quality=0.9)
    orphan = economic_score(effect_size=0.9, exposure_confidence=0.9, causal_path_quality=0.0)
    assert orphan.value < strong.value
    assert orphan.components["causal_path_quality"] == D(0)


# ── Приоритет ───────────────────────────────────────────────────────────────


def test_отсутствующий_рыночный_контекст_не_подменяется_средним():
    """«Не знаем, учтено ли в цене» — не «наполовину учтено» (§12.4)."""
    p = research_priority(economic=D("0.8"), evidence=D("0.8"), market=None)
    assert p.market_context_missing
    assert p.weights["market"] == 0.0
    # Веса перенормированы: сумма оставшихся — единица.
    assert abs(p.weights["economic"] + p.weights["evidence"] - 1.0) < 1e-9


def test_штраф_за_риск_снижает_приоритет():
    clean = research_priority(economic=D("0.8"), evidence=D("0.8"), market=D("0.5"))
    risky = research_priority(
        economic=D("0.8"), evidence=D("0.8"), market=D("0.5"), risk_penalty=D("0.5")
    )
    assert risky.value < clean.value


# ── Правило 3–2–1 ───────────────────────────────────────────────────────────


def strong_case(**overrides):
    base = dict(
        evidence=evidence_score(
            [item("a", "filing"), item("b", "state"), item("c", "market")],
            consecutive_confirmations=2,
        ),
        economic=economic_score(
            effect_size=0.8, exposure_confidence=0.8, causal_path_quality=0.8
        ),
        confirmations=2,
        causal_path_valid=True,
        entity_confidence=D("0.95"),
        contradictions=D("0.1"),
    )
    base.update(overrides)
    return three_two_one(**base)


def test_полный_набор_условий_подтверждает_гипотезу():
    assert strong_case().passed


def test_безупречная_цепочка_на_одном_источнике_не_подтверждается():
    """Ни одно условие не компенсируется другим.

    Один источник остаётся одним источником, какой бы стройной ни была
    причинная цепочка.
    """
    verdict = strong_case(
        evidence=evidence_score(
            [item("a", "filing"), item("a", "filing")], consecutive_confirmations=2
        )
    )
    assert not verdict.passed
    assert any("независимых источников" in f for f in verdict.failures)


def test_подтверждение_одним_способом_измерения_не_независимо():
    verdict = strong_case(
        evidence=evidence_score(
            [item("a", "filing"), item("b", "filing"), item("c", "filing")],
            consecutive_confirmations=2,
        )
    )
    assert not verdict.passed
    assert any("типов данных" in f for f in verdict.failures)


def test_половина_опровержений_запрещает_автоподтверждение():
    verdict = strong_case(contradictions=D("0.5"))
    assert not verdict.passed
    assert any("опровергающих" in f for f in verdict.failures)


def test_неуверенная_сущность_не_проходит():
    """Сигнал о чужой компании хуже, чем отсутствие сигнала."""
    verdict = strong_case(entity_confidence=D("0.80"))
    assert not verdict.passed


def test_причина_отказа_называет_все_несоответствия():
    verdict = three_two_one(
        evidence=evidence_score([item("a", "filing")], consecutive_confirmations=0),
        economic=economic_score(
            effect_size=0.1, exposure_confidence=0.1, causal_path_quality=0.0
        ),
        confirmations=0,
        causal_path_valid=False,
        entity_confidence=D("0.5"),
        contradictions=D(0),
    )
    assert not verdict.passed
    assert len(verdict.failures) >= 5
    assert verdict.reason


def test_доля_противоречий_считается_по_весу():
    items = [
        item("a", "filing", weight=3.0),
        item("b", "state", role=EvidenceRole.CONTRADICT, weight=1.0),
    ]
    assert contradiction_ratio(items) == D("0.2500")


def test_направление_даёт_знак_но_не_сторону_сделки():
    assert signed(ResearchDirection.POSITIVE, 0.7) == D("0.7")
    assert signed(ResearchDirection.NEGATIVE, 0.7) == D("-0.7")
    assert signed(ResearchDirection.NEUTRAL, 0.7) == D(0)
