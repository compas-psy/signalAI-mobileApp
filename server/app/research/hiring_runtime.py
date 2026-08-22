"""Production-переходник HIRING: «Работа России» → ранние гипотезы.

Первый запуск не ждёт восемь недель после деплоя. Дата публикации вакансий
позволяет построить ретроспективный baseline из текущей официальной выгрузки:
последние 28 дней сравниваются с предыдущими 56. Это даёт ранний кандидат,
но не подтверждение: один источник никогда не удовлетворяет правилу 3–2–1.
"""

from __future__ import annotations

import hashlib
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ResearchObservation
from ..models.enums import ResearchDirection
from .adapters import trudvsem
from .codes import observation_code
from .collect import Fetched
from .engines import hiring
from .fusion import Falsifier, SignalInput
from .issuers import Issuer, REGISTRY
from .market_context import for_hypothesis
from .pipeline import run as run_pipeline
from .policy import CollectionDenied, authorize
from .reach import USER_AGENT
from .timeline import visible_at

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


def _vacancy_lineage_root(row: trudvsem.VacancyDatum) -> str:
    """Стабильный корень одной вакансии через все её перепубликации."""
    identity = f"{trudvsem.SOURCE_ID}:vacancy:{row.vacancy_id}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _vacancy_observation_type(row: trudvsem.VacancyDatum) -> str:
    """Ключ конкретной информационной ревизии вакансии."""
    information_time = row.information_time
    revision = (
        information_time.astimezone(UTC).isoformat()
        if information_time is not None
        else "unknown"
    )
    return observation_code("hiring", row.vacancy_id, revision)


def _persist_vacancy_observation(
    session: Session,
    *,
    row: trudvsem.VacancyDatum,
    issuer: Issuer,
    first_seen_at: datetime,
    raw_sha256: str,
) -> bool:
    """Сохранить одну ревизию вакансии без перезаписи первого обнаружения.

    Повтор той же информационной ревизии не создаёт новый факт, даже если
    страница источника пришла другим набором байтов. Изменение
    ``information_time`` создаёт новую ревизию с тем же lineage root и
    ссылкой на предыдущую запись.
    """
    observation_type = _vacancy_observation_type(row)
    existing = session.execute(
        select(ResearchObservation.id).where(
            ResearchObservation.source_id == trudvsem.SOURCE_ID,
            ResearchObservation.entity_id == issuer.secid,
            ResearchObservation.observation_type == observation_type,
        )
    ).first()
    if existing is not None:
        return False

    lineage_root_id = _vacancy_lineage_root(row)
    previous = session.execute(
        select(ResearchObservation)
        .where(
            ResearchObservation.source_id == trudvsem.SOURCE_ID,
            ResearchObservation.entity_id == issuer.secid,
            ResearchObservation.lineage_root_id == lineage_root_id,
        )
        .order_by(
            ResearchObservation.revision_number.desc(),
            ResearchObservation.first_seen_at.desc(),
        )
        .limit(1)
    ).scalars().first()

    when = trudvsem.availability(row, first_seen_at=first_seen_at)
    session.add(
        ResearchObservation(
            observation_type=observation_type,
            entity_id=issuer.secid,
            source_id=trudvsem.SOURCE_ID,
            event_time=row.published_at,
            published_at=row.information_time,
            first_seen_at=first_seen_at,
            tradable_at=when.tradable_at,
            publication_time_uncertain=when.publication_time_uncertain,
            lineage_root_id=lineage_root_id,
            source_locator={
                "vacancy_id": row.vacancy_id,
                "employer_identity": row.employer_identity,
                "employer_name": row.employer_name,
                "title": row.title,
                "region_code": row.region_code,
                "region_name": row.region_name,
                "url": row.source_url,
                "source_created_at": (
                    row.published_at.isoformat() if row.published_at is not None else ""
                ),
                "source_modified_at": (
                    row.modified_at.isoformat() if row.modified_at is not None else ""
                ),
            },
            raw_sha256=raw_sha256,
            value_text=row.title,
            revision_number=(previous.revision_number + 1) if previous else 0,
            supersedes_id=previous.id if previous else None,
        )
    )
    return True


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
    information_time = row.information_time
    if information_time is None:
        return None
    age = max(0, (now - information_time).days)
    if age > 84:
        return None
    return hiring.Vacancy(
        employer_entity_id=issuer.secid,
        normalized_title=row.title,
        function=_function(row.title),
        region=row.region_name or row.region_code,
        published_day=information_time.date().toordinal(),
        salary=row.salary_mid,
        baseline_salary=None,
        age_days=age,
    )


def _runtime_vacancy(
    session: Session,
    *,
    row: trudvsem.VacancyDatum,
    issuer: Issuer,
    as_of: datetime,
) -> hiring.Vacancy | None:
    """Return a vacancy only after its durable observation becomes usable.

    Runtime decisions must use the persisted information axis, not recompute
    availability from the current collection time. Re-fetching the same
    vacancy therefore cannot move first-seen forward or manufacture a new
    confirmation, while a newly discovered revision remains invisible until
    its stored ``tradable_at`` boundary is reached.
    """
    observation = session.execute(
        select(ResearchObservation).where(
            ResearchObservation.source_id == trudvsem.SOURCE_ID,
            ResearchObservation.entity_id == issuer.secid,
            ResearchObservation.observation_type == _vacancy_observation_type(row),
        )
    ).scalars().first()
    if observation is None or not visible_at(observation.tradable_at, as_of):
        return None
    return _vacancy(row, issuer, as_of)


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

    raw_rows = [
        (datum, page.sha256)
        for page in fetched
        for datum in trudvsem.parse(page)
    ]
    report.vacancies = len(raw_rows)
    by_issuer: dict[str, tuple[Issuer, list[hiring.Vacancy]]] = {}
    for row, raw_sha256 in raw_rows:
        issuer = _issuer(row)
        if issuer is None:
            continue
        _persist_vacancy_observation(
            session,
            row=row,
            issuer=issuer,
            first_seen_at=moment,
            raw_sha256=raw_sha256,
        )
        vacancy = _runtime_vacancy(
            session,
            row=row,
            issuer=issuer,
            as_of=moment,
        )
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