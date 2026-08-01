"""Разрешение сущностей и экономические связи (ТЗ §9).

Сигнал о чужой компании хуже, чем отсутствие сигнала: он выглядит
одинаково убедительно и ведёт не туда. Поэтому уверенность в сопоставлении
входит в правило 3–2–1 отдельным порогом (0.90), а не растворяется в общей
оценке.

Порядок разрешения из §9.1 соблюдается буквально, и он не про удобство.
Сильный идентификатор — ИНН, ОГРН, ISIN, пара «сеть + адрес контракта» —
даёт единицу, потому что он уникален по построению. Совпадение
нормализованного имени с юрисдикцией даёт много, но не единицу: тёзки
существуют. Нечёткое совпадение годится только для поиска кандидатов и в
подтверждённый сигнал не попадает никогда.

Модель может **предложить** кандидата, но не может объединить сущности без
сильного идентификатора (§9.1). Это не недоверие к модели, а свойство
задачи: ошибка объединения не обнаруживается по результату — она делает
результат уверенно неверным.

Про экономическую связь. Связь без доли — это «компания как-то связана с
темой». §9.4 ограничивает такую связь потолком 0.50 и запрещает говорить о
материальном влиянии. Формулировка «может отразиться» и «отразится» — это
разные утверждения, и разница между ними здесь выражена числом.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

# Уверенность по §9.3.
STRONG_ID = Decimal("1.00")
MULTI_ATTRIBUTE = Decimal("0.95")
NAME_AND_JURISDICTION = Decimal("0.85")
FUZZY = Decimal("0.70")
UNRESOLVED = Decimal("0.00")

# Ниже этого сопоставление не участвует в автоматическом сигнале (§9.3).
MIN_AUTOMATIC = Decimal("0.75")

# Потолок релевантности при неизвестной доле (§9.4).
UNKNOWN_EXPOSURE_CAP = Decimal("0.50")

# Сильные идентификаторы. Пара «сеть + адрес» считается одним.
STRONG_TYPES = frozenset({"INN", "OGRN", "ISIN", "LEI", "MOEX_SECID", "CHAIN_ADDRESS"})

# Организационно-правовые формы: отделяются, но не выбрасываются — «Роснефть»
# и «Роснефть-Логистика» разные компании, а ООО и ПАО с одним названием
# бывают разными юридическими лицами.
LEGAL_FORMS = (
    "публичное акционерное общество",
    "акционерное общество",
    "общество с ограниченной ответственностью",
    "пао",
    "оао",
    "зао",
    "ао",
    "ооо",
    "нко",
    "гуп",
    "муп",
)


@dataclass(frozen=True, slots=True)
class Identifier:
    kind: str
    value: str

    @property
    def strong(self) -> bool:
        return self.kind in STRONG_TYPES


@dataclass(frozen=True, slots=True)
class Candidate:
    """Сущность-кандидат на сопоставление."""

    entity_id: str
    name: str
    jurisdiction: str = "RU"
    identifiers: tuple[Identifier, ...] = ()
    domain: str = ""
    parent_entity_id: str = ""


@dataclass(frozen=True, slots=True)
class Query:
    """То, что нужно сопоставить."""

    name: str = ""
    jurisdiction: str = "RU"
    identifiers: tuple[Identifier, ...] = ()
    domain: str = ""
    parent_entity_id: str = ""


@dataclass(frozen=True, slots=True)
class Match:
    entity_id: str
    confidence: Decimal
    method: str
    detail: str = ""

    @property
    def automatic(self) -> bool:
        """Годится ли для автоматического сигнала (§9.3)."""
        return self.confidence >= MIN_AUTOMATIC


def normalize_name(raw: str) -> tuple[str, str]:
    """Нормализовать наименование. Возвращает (имя, организационная форма).

    Форма отделяется, но сохраняется: «ООО Ромашка» и «ПАО Ромашка» — это
    разные юридические лица, и склеивать их нельзя. Кириллица и латиница
    приводятся к одному виду только как псевдоним, каноническим
    идентификатором это не становится (§9.2).
    """
    text = raw.strip().lower()
    text = text.replace("«", " ").replace("»", " ").replace('"', " ")
    text = re.sub(r"\s+", " ", text).strip()
    form = ""
    for candidate in LEGAL_FORMS:
        if text.startswith(candidate + " "):
            form = candidate
            text = text[len(candidate) + 1 :]
            break
        if text.endswith(" " + candidate):
            form = candidate
            text = text[: -len(candidate) - 1]
            break
    return re.sub(r"\s+", " ", text).strip(), form


def _strong_overlap(a: tuple[Identifier, ...], b: tuple[Identifier, ...]) -> Identifier | None:
    known = {(i.kind, i.value) for i in a if i.strong}
    for identifier in b:
        if identifier.strong and (identifier.kind, identifier.value) in known:
            return identifier
    return None


def resolve(query: Query, candidates: list[Candidate]) -> Match | None:
    """Сопоставить по порядку §9.1.

    Возвращает лучшее сопоставление или None. Порядок проверок и есть
    правило: сначала то, что уникально по построению, и только потом то,
    что похоже.
    """
    # 1. Сильный идентификатор.
    for candidate in candidates:
        hit = _strong_overlap(query.identifiers, candidate.identifiers)
        if hit is not None:
            return Match(
                candidate.entity_id,
                STRONG_ID,
                "strong_identifier",
                f"совпал {hit.kind} {hit.value}",
            )

    if not query.name:
        return None

    wanted, wanted_form = normalize_name(query.name)

    # 2. Точное имя плюс юрисдикция.
    exact = [
        c
        for c in candidates
        if normalize_name(c.name)[0] == wanted and c.jurisdiction == query.jurisdiction
    ]
    if len(exact) == 1:
        candidate = exact[0]
        other_form = normalize_name(candidate.name)[1]
        if wanted_form and other_form and wanted_form != other_form:
            # Одинаковое название при разной форме — разные лица.
            return Match(
                candidate.entity_id,
                FUZZY,
                "name_form_mismatch",
                f"формы различаются: {wanted_form} против {other_form}",
            )
        return Match(
            candidate.entity_id,
            NAME_AND_JURISDICTION,
            "name_jurisdiction",
            "имя и юрисдикция совпали, сильного идентификатора нет",
        )
    if len(exact) > 1:
        # 3. Дополнительные атрибуты: домен и родитель.
        scored = [
            (
                c,
                (1 if query.domain and c.domain == query.domain else 0)
                + (
                    1
                    if query.parent_entity_id
                    and c.parent_entity_id == query.parent_entity_id
                    else 0
                ),
            )
            for c in exact
        ]
        scored.sort(key=lambda pair: -pair[1])
        best, points = scored[0]
        if points >= 2:
            return Match(
                best.entity_id,
                MULTI_ATTRIBUTE,
                "multi_attribute",
                "совпали имя, домен и родительская компания",
            )
        # Тёзки из разных регионов не объединяются (§9.2).
        return Match(
            best.entity_id,
            FUZZY,
            "ambiguous_name",
            f"одноимённых кандидатов {len(exact)}: нужен сильный идентификатор",
        )

    # 4. Нечёткое совпадение — только для поиска.
    for candidate in candidates:
        other, _ = normalize_name(candidate.name)
        if other.startswith(wanted[:6]) or wanted.startswith(other[:6]):
            return Match(
                candidate.entity_id,
                FUZZY,
                "fuzzy",
                "похожее наименование: годится только для поиска кандидатов",
            )
    return None


@dataclass(frozen=True, slots=True)
class Exposure:
    """Экономическая связь сущности с драйвером (§9.4)."""

    entity_id: str
    driver: str
    # Один из доказанных видов: доля сегмента, доля клиента, физический
    # объём, мощность, механизм value capture.
    evidence_kind: str = ""
    share: Decimal | None = None

    @property
    def unknown(self) -> bool:
        return self.share is None

    @property
    def relevance(self) -> Decimal:
        """Экономическая релевантность для оценки.

        Без доли — потолок 0.50 и никаких утверждений о материальности.
        Связь «компания как-то связана с темой» существует, но говорить по
        ней о влиянии на выручку нельзя.
        """
        if self.share is None:
            return UNKNOWN_EXPOSURE_CAP if self.evidence_kind else Decimal(0)
        return min(Decimal(1), max(Decimal(0), self.share))

    @property
    def wording(self) -> str:
        """Как об этой связи разрешено говорить."""
        if self.share is None:
            return "может отразиться"
        if self.share >= Decimal("0.1"):
            return "отразится заметно"
        return "отразится незначительно"


__all__ = [
    "Candidate",
    "Exposure",
    "FUZZY",
    "Identifier",
    "LEGAL_FORMS",
    "MIN_AUTOMATIC",
    "MULTI_ATTRIBUTE",
    "Match",
    "NAME_AND_JURISDICTION",
    "Query",
    "STRONG_ID",
    "STRONG_TYPES",
    "UNKNOWN_EXPOSURE_CAP",
    "normalize_name",
    "resolve",
]
