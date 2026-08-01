"""Разрешение сущностей §9.

Сигнал о чужой компании хуже, чем отсутствие сигнала: он выглядит
одинаково убедительно и ведёт не туда. Тесты проверяют, что похожесть
не превращается в уверенность.
"""

from __future__ import annotations

from decimal import Decimal

from app.research.entities import (
    MIN_AUTOMATIC,
    STRONG_ID,
    Candidate,
    Exposure,
    Identifier,
    Query,
    normalize_name,
    resolve,
)

D = Decimal


def candidate(entity_id: str, name: str, **kw) -> Candidate:
    return Candidate(entity_id=entity_id, name=name, **kw)


# ── Нормализация ────────────────────────────────────────────────────────────


def test_форма_отделяется_но_не_выбрасывается():
    """«ООО Ромашка» и «ПАО Ромашка» — разные юридические лица."""
    name, form = normalize_name('ООО "Ромашка"')
    assert name == "ромашка"
    assert form == "ооо"


def test_кавычки_и_пробелы_нормализуются():
    assert normalize_name("  ПАО   «Газпром»  ")[0] == "газпром"


def test_форма_в_конце_тоже_распознаётся():
    assert normalize_name("Ромашка ООО") == ("ромашка", "ооо")


# ── Порядок разрешения §9.1 ─────────────────────────────────────────────────


def test_сильный_идентификатор_даёт_единицу():
    """ИНН уникален по построению — сомневаться не в чем."""
    match = resolve(
        Query(identifiers=(Identifier("INN", "7736050003"),)),
        [candidate("e-1", "Газпром",
                   identifiers=(Identifier("INN", "7736050003"),))],
    )
    assert match.confidence == STRONG_ID
    assert match.automatic


def test_совпадение_имени_не_даёт_единицы():
    """Тёзки существуют, и уверенность обязана это отражать."""
    match = resolve(Query(name="Ромашка"), [candidate("e-1", "Ромашка")])
    assert match.confidence < STRONG_ID
    assert match.automatic


def test_одноимённые_компании_не_объединяются_молча():
    """§9.2: одноимённые компании из разных регионов — разные компании."""
    match = resolve(
        Query(name="Ромашка"),
        [candidate("e-1", "Ромашка"), candidate("e-2", "Ромашка")],
    )
    assert match.method == "ambiguous_name"
    assert not match.automatic
    assert "сильный идентификатор" in match.detail


def test_домен_и_родитель_разрешают_неоднозначность():
    match = resolve(
        Query(name="Ромашка", domain="romashka.ru", parent_entity_id="p-1"),
        [
            candidate("e-1", "Ромашка", domain="other.ru"),
            candidate("e-2", "Ромашка", domain="romashka.ru",
                      parent_entity_id="p-1"),
        ],
    )
    assert match.entity_id == "e-2"
    assert match.automatic


def test_разная_форма_при_одном_имени_снижает_уверенность():
    match = resolve(
        Query(name='ООО "Ромашка"'), [candidate("e-1", 'ПАО "Ромашка"')]
    )
    assert not match.automatic
    assert "формы различаются" in match.detail


def test_нечёткое_совпадение_не_годится_для_автоматики():
    """§9.1: fuzzy — только для поиска кандидатов."""
    match = resolve(Query(name="Ромашкино"), [candidate("e-1", "Ромашка-Инвест")])
    assert match.method == "fuzzy"
    assert not match.automatic
    assert match.confidence < MIN_AUTOMATIC


def test_ничего_похожего_не_даёт_сопоставления():
    assert resolve(Query(name="Ромашка"), [candidate("e-1", "Лукойл")]) is None


def test_адрес_контракта_считается_сильным_идентификатором():
    """Для крипты тикер идентификатором не является, а пара сеть+адрес — да."""
    match = resolve(
        Query(identifiers=(Identifier("CHAIN_ADDRESS", "1:0xabc"),)),
        [candidate("t-1", "Protocol",
                   identifiers=(Identifier("CHAIN_ADDRESS", "1:0xabc"),))],
    )
    assert match.confidence == STRONG_ID


# ── Экономическая связь §9.4 ────────────────────────────────────────────────


def test_связь_без_доли_ограничена_потолком():
    """«Компания как-то связана с темой» — не материальное влияние."""
    link = Exposure("e-1", "госпрограмма", evidence_kind="qualitative")
    assert link.unknown
    assert link.relevance == D("0.50")
    assert link.wording == "может отразиться"


def test_связь_без_доказательства_не_даёт_релевантности():
    assert Exposure("e-1", "тема").relevance == D(0)


def test_измеренная_доля_разрешает_говорить_о_влиянии():
    link = Exposure("e-1", "контракт", evidence_kind="customer_share",
                    share=D("0.25"))
    assert link.relevance == D("0.25")
    assert link.wording == "отразится заметно"


def test_ничтожная_доля_называется_ничтожной():
    link = Exposure("e-1", "контракт", evidence_kind="customer_share",
                    share=D("0.01"))
    assert link.wording == "отразится незначительно"
