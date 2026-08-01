"""Ограничения LLM-контура и разбор вердикта критика (ТЗ §10.4, §15).

Модель здесь — инструмент разбора текста, а не источник фактов. §15.1
перечисляет разрешённое и запрещённое, и запрещённое важнее: придумать
отсутствующее значение, посчитать оценку вместо кода, определить время
публикации «по смыслу», самостоятельно повысить состояние гипотезы.

Всё это — вещи, которые модель делает охотно и убедительно, поэтому защита
от них построена не на инструкции в промпте, а на проверке результата.
Инструкцию можно перебить текстом внутри документа; проверку — нельзя.

Три слоя защиты, и все три чисто вычислительные.

**Утверждение без ссылки на фрагмент отклоняется** (§10.4). Не «понижается
в доверии», а отклоняется: утверждение, которое нельзя проверить по
сохранённому документу, не является наблюдением ни в каком приближении.

**Числа сверяются с расчётом.** Модель пишет объяснение по готовым числам,
и любое расхождение означает, что она посчитала сама. §15.1 это запрещает,
и проверка ловит нарушение независимо от того, как оно возникло.

**Документ — недоверенные данные.** Текст внутри источника не может менять
инструкции. Попытка это сделать не выбрасывается молча: она регистрируется,
потому что документ, который просит игнорировать предыдущие указания, —
это событие, а не шум.

Самого вызова модели здесь нет: на сервере нет ключа, и §26.2 требует
называть такое блокером, а не изображать работу. Всё, что можно проверять
без модели, проверяется.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum

# Что критик может ответить (§15.3).
class Verdict(StrEnum):
    PASS = "pass"
    NEEDS_EVIDENCE = "needs_evidence"
    REJECT = "reject"
    TEMPORAL_ERROR = "temporal_error"


# Сколько раз пробуем починить структурно неверный ответ (§15.4).
MAX_REPAIR_ATTEMPTS = 2

# Признаки попытки перебить инструкции из тела документа. Список намеренно
# короткий и по существу: это не фильтр «плохих слов», а детектор
# конкретного приёма.
INJECTION_PATTERNS = (
    r"игнорируй\s+(все\s+)?предыдущие",
    r"ignore\s+(all\s+)?previous",
    r"disregard\s+(all\s+)?prior",
    r"new\s+system\s+prompt",
    r"ты\s+теперь\s+",
    r"you\s+are\s+now\s+",
    r"</?system>",
    r"<\|im_start\|>",
)

# Слова, которых не должно быть в выдаче ни при каких обстоятельствах
# (§15.1, §16.4).
FORBIDDEN_OUTPUT = ("buy", "sell", "покупать", "продавать", "гарантированн")


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Ссылка на фрагмент сохранённого документа (§10.4)."""

    document_id: str
    start_char: int
    end_char: int
    quote_sha256: str

    def matches(self, document_text: str) -> bool:
        """Совпадает ли хеш цитаты с тем, что реально лежит в документе.

        Проверка не формальная: модель охотно цитирует близко к тексту, но
        не дословно, и «близко» здесь означает «другой факт».
        """
        if self.start_char < 0 or self.end_char > len(document_text):
            return False
        quote = document_text[self.start_char : self.end_char]
        return hashlib.sha256(quote.encode()).hexdigest() == self.quote_sha256


@dataclass(frozen=True, slots=True)
class Claim:
    """Утверждение, извлечённое моделью."""

    claim_id: str
    text: str
    span: SourceSpan | None = None
    value: Decimal | None = None


@dataclass
class ValidationReport:
    accepted: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    injection_attempts: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.rejected and not self.injection_attempts


def detect_injection(text: str) -> list[str]:
    """Найти попытки перебить инструкции текстом документа.

    Документ — недоверенные данные (§15.4, §21.3). Найденное не
    выбрасывается молча: документ, который просит игнорировать предыдущие
    указания, — это событие для журнала качества данных.
    """
    found: list[str] = []
    lowered = text.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            found.append(pattern)
    return found


def validate_claims(
    claims: list[Claim],
    documents: dict[str, str],
    *,
    computed: dict[str, Decimal] | None = None,
) -> ValidationReport:
    """Проверить утверждения модели (§10.4, §15.4).

    Отклоняется, а не понижается в доверии: утверждение, которое нельзя
    проверить по сохранённому документу, не является наблюдением.
    """
    report = ValidationReport()
    numbers = computed or {}

    for claim in claims:
        attempts = detect_injection(claim.text)
        if attempts:
            report.injection_attempts.extend(attempts)
            report.rejected[claim.claim_id] = (
                "текст утверждения содержит попытку изменить инструкции"
            )
            continue
        if claim.span is None:
            report.rejected[claim.claim_id] = "нет ссылки на фрагмент источника"
            continue
        document = documents.get(claim.span.document_id)
        if document is None:
            report.rejected[claim.claim_id] = "документ не найден в хранилище"
            continue
        if not claim.span.matches(document):
            report.rejected[claim.claim_id] = (
                "цитата не совпадает с сохранённым документом"
            )
            continue
        if claim.value is not None and claim.claim_id in numbers:
            if claim.value != numbers[claim.claim_id]:
                # Модель посчитала сама. §15.1 это запрещает, и поймать
                # такое можно только сверкой.
                report.rejected[claim.claim_id] = (
                    f"число {claim.value} не совпадает с расчётом "
                    f"{numbers[claim.claim_id]}"
                )
                continue
        report.accepted.append(claim.claim_id)
    return report


def numbers_in(text: str) -> list[Decimal]:
    """Все числа в тексте — для сверки объяснения с расчётом."""
    result: list[Decimal] = []
    for token in re.findall(r"-?\d+(?:[.,]\d+)?", text):
        try:
            result.append(Decimal(token.replace(",", ".")))
        except InvalidOperation:
            continue
    return result


def explanation_is_safe(
    text: str, allowed_numbers: list[Decimal]
) -> tuple[bool, str]:
    """Можно ли показывать это объяснение владельцу.

    Два условия. Все числа обязаны быть из расчёта — объяснение не место
    для новых величин. И никаких торговых команд: §16.4 запрещает BUY,
    SELL и обещание доходности, и запрет проверяется, а не подразумевается.
    """
    lowered = text.lower()
    for word in FORBIDDEN_OUTPUT:
        if word in lowered:
            return False, f"в тексте есть запрещённое слово «{word}»"
    allowed = set(allowed_numbers)
    for number in numbers_in(text):
        if number not in allowed:
            return False, f"число {number} отсутствует в расчёте"
    return True, ""


@dataclass(frozen=True, slots=True)
class CriticResult:
    """Разобранный вердикт критика (§15.3)."""

    verdict: Verdict
    duplicate_lineage_detected: bool = False
    causal_path_complete: bool = True
    falsifiers_are_observable: bool = True
    unsupported_claim_ids: tuple[str, ...] = ()
    required_actions: tuple[str, ...] = ()
    summary: str = ""

    @property
    def blocks_confirmation(self) -> bool:
        """Мешает ли вердикт подтвердить гипотезу.

        Критик не понижает и не повышает состояние сам — §15.1 запрещает.
        Он называет причину, а решение принимает объединение.
        """
        return (
            self.verdict is not Verdict.PASS
            or self.duplicate_lineage_detected
            or not self.causal_path_complete
            or not self.falsifiers_are_observable
        )


def parse_verdict(payload: dict) -> CriticResult:
    """Разобрать ответ модели строго по схеме.

    Незнакомый вердикт читается как ``reject``, а не как ``pass``: ошибка
    разбора не должна давать больше прав, чем было.
    """
    raw = str(payload.get("verdict", ""))
    try:
        verdict = Verdict(raw)
    except ValueError:
        verdict = Verdict.REJECT

    return CriticResult(
        verdict=verdict,
        duplicate_lineage_detected=bool(payload.get("duplicate_lineage_detected")),
        causal_path_complete=bool(payload.get("causal_path_complete", False)),
        falsifiers_are_observable=bool(
            payload.get("falsifiers_are_observable", False)
        ),
        unsupported_claim_ids=tuple(
            str(x) for x in payload.get("unsupported_claim_ids", ())
        ),
        required_actions=tuple(str(x) for x in payload.get("required_actions", ())),
        summary=str(payload.get("summary", ""))[:2000],
    )


class GatewayUnavailable(RuntimeError):
    """Модельного доступа нет.

    Отдельный тип: §26.2 требует называть отсутствие доступа блокером, а не
    молча пропускать шаг. Гипотеза без разбора критиком остаётся в своём
    состоянии — она не становится ни лучше, ни хуже от того, что критика не
    спросили.
    """


__all__ = [
    "Claim",
    "CriticResult",
    "FORBIDDEN_OUTPUT",
    "GatewayUnavailable",
    "INJECTION_PATTERNS",
    "MAX_REPAIR_ATTEMPTS",
    "SourceSpan",
    "ValidationReport",
    "Verdict",
    "detect_injection",
    "explanation_is_safe",
    "numbers_in",
    "parse_verdict",
    "validate_claims",
]
