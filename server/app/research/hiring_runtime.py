"""Production-переходник HIRING: «Работа России» → ранние гипотезы.

Первый запуск не ждёт восемь недель после деплоя. Дата публикации вакансий
позволяет построить ретроспективный baseline из текущей официальной выгрузки:
последние 28 дней сравниваются с предыдущими 56. Это даёт ранний кандидат,
но не подтверждение: один источник никогда не удовлетворяет правилу 3–2–1.
"""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from .adapters import trudvsem
from .collect import Fetched
from .engines import hiring
from .fusion import Falsifier, SignalInput
from .issuers import Issuer, REGISTRY
from .market_context import for_hypothesis
from .pipeline import run as run_pipeline
from .policy import CollectionDenied, authorize
from .reach import USER_AGENT
from ..models.enums import ResearchDirection

MAX_PAGES = 100  # API сам ограничивает полезную выборку 10k строками.
TIMEOUT = 30


@dataclass
class HiringRunReport:
    fetched_pages: int = 0
    vacancies: int = 0
    matched: int = 0
    issuers: int = 0
    signals: int = 0
    hypotheses: int = 0
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"HIRING страниц {self.fetched_pages}",
            f"вакансий {self.vacancies}",
            f"сопоставлено {self.matched}",
            f"эмитентов {self.issuers}",
            f"сигналов {self.signals}",
            f"гипотез {self.hypotheses}",
        ]
        if self.skipped:
            parts.append(f"пропущено {len(self.skipped)}: {self.skipped[0]}")
        return ", ".join(parts)


def _norm(value: str) -> str:
    value = value.lower().replace("ё", "е")
    value = re.sub(r"\b(пао|ао|ооо|оао|зао|гк|нк)\b", " ", value)
    value = re.sub(r"[^a-zа-я0-9]+", " ", value)
    return " ".join(value.split())


# Несколько общеупотребимых брендов отличаются от юридического названия.
ALIASES: dict[str, tuple[str, ...]] = {
    "SBER": ("сбер", "сбербанк"),
    "GAZP": ("газпром",),
    "VTBR": ("втб",),
    "LKOH": ("лукойл", "lukoil"),
    "ROSN": ("роснефть",),
    "TATN": ("татнефть",),
    "NVTK": ("новатэк", "novatek"),
    "GMKN": ("норильский никель", "норникель"),
    "ALRS": ("алроса",),
    "PLZL": ("полюс",),
    "CHMF": ("северсталь",),
    "NLMK": ("нлмк",),
    "MAGN": ("ммк", "магнитогорский металлургический"),
    "PHOR": ("фосагро",),
    "MGNT": ("магнит",),
    "MTSS": ("мтс", "мобильные телесистемы"),
    "RTKM": ("ростелеком",),
    "AFLT": ("аэрофлот",),
    "TRNFP": ("транснефть",),
    "HYDR": ("русгидро",),
    "IRAO": ("интер рао",),
    "PIKK": ("пик",),
    "KMAZ": ("камаз",),
}


def _issuer(row: trudvsem.VacancyDatum) -> Issuer | None:
    # Если источник дал сильный идентификатор, он либо совпадает точно, либо
    # строка не сопоставляется вообще. Откат к бренду после чужого ИНН
    # превращал доказанное другое юрлицо в ложное совпадение.
    if row.employer_inn:
        for issuer in REGISTRY:
            if issuer.inn and issuer.inn == row.employer_inn:
                return issuer
        return None

    company = _norm(row.employer_name)
    if not company:
        return None
    matches: list[Issuer] = []
    for issuer in REGISTRY:
        aliases = ALIASES.get(issuer.secid, (_norm(issuer.name),))
        if any(alias and _norm(alias) == company for alias in aliases):
            matches.append(issuer)
    # Не выбираем из неоднозначности. Ложная привязка опаснее пропуска.
    return matches[0] if len(matches) == 1 else None


def _function(title: str) -> str:
    t = _norm(title)
    rules = (
        ("procurement", ("закуп", "снабж", "категорийный менеджер")),
        ("installation", ("монтаж", "налад", "строител", "пусконалад")),
        ("engineering", ("инженер", "разработчик", "архитектор", "технолог", "конструктор", "data scientist", "аналитик данных")),
        ("operations", ("оператор", "производств", "машинист", "аппаратчик", "рабочий", "диспетчер")),
        ("sales", ("продаж", "аккаунт", "клиентский менеджер", "торговый представитель")),
        ("service", ("сервис", "поддержк", "обслуживан", "ремонт")),
        ("compliance", ("комплаенс", "риск", "внутренний контроль", "безопасност")),
        ("corporate", ("бухгалтер", "финанс", "юрист", "hr", "персонал", "кадр")),
    )
    for function, needles in rules:
        if any(needle in t for needle in needles):
            return function
    return "corporate"


def _fetch(url: str, now: datetime) -> Fetched | None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read(8 * 1024 * 1024)
            return Fetched(
                url=url,
                status=response.status,
                body=body,
                requested_at=now,
                responded_at=datetime.now(UTC),
                headers={k.lower(): v for k, v in response.headers.items()},
                content_type=response.headers.get("Content-Type", ""),
            )
    except Exception:  # источник не должен уронить весь research tick
        return None


def _vacancy(row: trudvsem.VacancyDatum, issuer: Issuer, now: datetime) -> hiring.Vacancy | None:
    published = row.published_at or row.modified_at
    if published is None:
        return None
    age = max(0, (now - published).days)
    if age > 84:
        return None
    return hiring.Vacancy(
        employer_entity_id=issuer.secid,
        normalized_title=row.title,
        function=_function(row.title),
        region=row.region_name or row.region_code,
        published_day=published.date().toordinal(),
        salary=row.salary_mid,
        baseline_salary=None,
        age_days=age,
    )


def run_hiring_live(session: Session, *, now: datetime | None = None) -> HiringRunReport:
    moment = now or datetime.now(UTC)
    report = HiringRunReport()
    try:
        authorize(session, trudvsem.SOURCE_ID, {"fetch", "transform"}, now=moment)
    except CollectionDenied as denied:
        report.skipped.append(f"{trudvsem.SOURCE_ID}: {denied.reason}")
        return report

    modified_from = (moment - timedelta(days=84)).isoformat().replace("+00:00", "Z")
    first = _fetch(trudvsem.page_url(modified_from=modified_from), moment)
    if first is None or not first.ok:
        report.skipped.append("Работа России не ответила")
        return report
    fetched = [first]
    report.fetched_pages = 1
    pages = trudvsem.remaining_pages(first, limit=trudvsem.PAGE_LIMIT)
    for obj in pages[: MAX_PAGES - 1]:
        page = _fetch(obj.url, moment)
        if page is None or not page.ok:
            continue
        fetched.append(page)
        report.fetched_pages += 1

    raw_rows = [datum for page in fetched for datum in trudvsem.parse(page)]
    report.vacancies = len(raw_rows)
    by_issuer: dict[str, tuple[Issuer, list[hiring.Vacancy]]] = {}
    for row in raw_rows:
        issuer = _issuer(row)
        if issuer is None:
            continue
        vacancy = _vacancy(row, issuer, moment)
        if vacancy is None:
            continue
        report.matched += 1
        by_issuer.setdefault(issuer.secid, (issuer, []))[1].append(vacancy)
    report.issuers = len(by_issuer)

    signals: list[SignalInput] = []
    issuer_results: dict[str, hiring.HiringResult] = {}
    for secid, (issuer, rows) in by_issuer.items():
        recent = [v for v in rows if v.age_days <= 28]
        baseline_rows = [v for v in rows if 28 < v.age_days <= 84]
        if len(recent) < 3 or len(baseline_rows) < 3:
            report.skipped.append(f"{secid}: мало вакансий для структуры")
            continue
        baseline: dict[str, int] = {}
        for role in baseline_rows:
            baseline[role.function] = baseline.get(role.function, 0) + 1
        known_regions = frozenset(v.region for v in baseline_rows if v.region)
        result = hiring.evaluate(
            recent,
            baseline_functions=baseline,
            known_regions=known_regions,
            weeks_observed=12,
            snapshot_coverage=1.0,
            supplier_confirmation=False,
        )
        issuer_results[secid] = result
        if not result.applicable or result.direction != "positive":
            continue
        signals.append(
            SignalInput(
                strategy_key=hiring.STRATEGY_KEY,
                entity_id=issuer.secid,
                instrument_id=f"MOEX:EQ:{issuer.secid}",
                direction=ResearchDirection.POSITIVE,
                strength=result.strength,
                target_kpi_family="revenue_capacity",
                causal_driver="capacity_expansion_hiring",
                window_from_days=60,
                window_to_days=365,
                reason_codes=tuple(result.reason_codes),
                detail=f"{issuer.name}: {result.detail}; уникальных ролей {result.unique_roles}",
            )
        )

    report.signals = len(signals)
    if not signals:
        return report

    def resolve(bucket: list[SignalInput]) -> dict:
        head = bucket[0]
        issuer = next((i for i in REGISTRY if i.secid == head.entity_id), None)
        confidence = issuer.confidence if issuer else Decimal("0.4")
        market = for_hypothesis(
            session,
            instrument_id=head.instrument_id,
            direction=head.direction,
        )
        return {
            # Один официальный источник = ранний кандидат, не подтверждение.
            "confirmations": 1,
            "entity_confidence": confidence,
            "effect_size": float(abs(head.strength)),
            "exposure_confidence": min(0.8, float(confidence) * 0.8),
            "falsifiers": [
                Falsifier(
                    description="структура вакансий возвращается к baseline два периода подряд",
                    metric_or_event="hiring_structural_shift",
                    operator="<",
                    threshold=0.10,
                    check_frequency="P1W",
                )
            ],
            "market_context": market.score,
            "market_context_state": market.state,
            "market_context_detail": market.detail,
        }

    outcome = run_pipeline(session, signals, resolve=resolve, now=moment)
    report.hypotheses = outcome.created + outcome.updated
    return report


__all__ = ["HiringRunReport", "run_hiring_live"]
