"""Адаптер открытого API «Работа России» для движка HIRING (§10, §13.3).

Источник отдаёт вакансии, а не инвестиционные выводы. Этот модуль знает только
контракт API: пагинацию, инкремент по ``modifiedFrom``, канонические поля
работодателя и две временные оси. Классификация функций найма, привязка к
эмитенту и решение о сигнале живут выше — в переходнике и движке.

Контакты из вакансии намеренно не читаются и не сохраняются. Для структуры
найма нужны роль, работодатель, регион, зарплатная вилка и время публикации;
телефоны, email и имена контактных лиц инвестиционной задаче не нужны.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from math import ceil
from urllib.parse import urlencode

from ..collect import Cursor, Fetched, RemoteObject
from ..policy import Permit
from ..timeline import Availability, tradable_at

SOURCE_ID = "trudvsem"
BASE_URL = "https://opendata.trudvsem.ru/api/v1/vacancies"
PAGE_LIMIT = 100
MAX_RESULT_ROWS = 10_000


@dataclass(frozen=True, slots=True)
class VacancyDatum:
    vacancy_id: str
    employer_code: str
    employer_inn: str
    employer_ogrn: str
    employer_name: str
    title: str
    region_code: str
    region_name: str
    published_at: datetime | None
    modified_at: datetime | None
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    source_url: str = ""

    @property
    def employer_identity(self) -> str:
        return self.employer_inn or self.employer_ogrn or self.employer_code

    @property
    def information_time(self) -> datetime | None:
        return self.modified_at or self.published_at

    @property
    def salary_mid(self) -> Decimal | None:
        if self.salary_min is None and self.salary_max is None:
            return None
        if self.salary_min is None:
            return self.salary_max
        if self.salary_max is None:
            return self.salary_min
        return (self.salary_min + self.salary_max) / Decimal(2)


def _moment(raw: object) -> datetime | None:
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _decimal(raw: object) -> Decimal | None:
    if raw is None:
        return None
    value = str(raw).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if value.lower() in {"", "-", "—", "null", "none", "n/a"}:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _text(mapping: dict, *names: str) -> str:
    for name in names:
        value = mapping.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _mapping(raw: object) -> dict:
    return raw if isinstance(raw, dict) else {}


def _first_address(vacancy: dict) -> dict:
    addresses = vacancy.get("addresses")
    if isinstance(addresses, list):
        for item in addresses:
            if isinstance(item, dict):
                return item
    return {}


def _rows(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    raw = results.get("vacancies") if isinstance(results, dict) else payload.get("vacancies")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def parse(fetched: Fetched) -> list[VacancyDatum]:
    if not fetched.ok:
        return []
    try:
        payload = json.loads(fetched.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict) and str(payload.get("status", "200")) != "200":
        return []

    result: list[VacancyDatum] = []
    for wrapper in _rows(payload):
        vacancy = _mapping(wrapper.get("vacancy")) or wrapper
        company = _mapping(vacancy.get("company"))
        region = _mapping(vacancy.get("region"))
        address_region = _mapping(_first_address(vacancy).get("region"))
        vacancy_id = _text(vacancy, "id", "vacancy-id", "vacancy_id")
        title = _text(vacancy, "job-name", "job_name", "name", "profession")
        employer_code = _text(company, "companycode", "company-code", "company_code", "id") or _text(vacancy, "companycode", "company-code", "company_code")
        employer_inn = _text(company, "inn", "company-inn", "company_inn") or _text(vacancy, "inn", "company-inn", "company_inn")
        employer_ogrn = _text(company, "ogrn", "company-ogrn", "company_ogrn") or _text(vacancy, "ogrn", "company-ogrn", "company_ogrn")
        if not vacancy_id or not title or not (employer_code or employer_inn or employer_ogrn):
            continue
        result.append(VacancyDatum(
            vacancy_id=vacancy_id,
            employer_code=employer_code,
            employer_inn=employer_inn,
            employer_ogrn=employer_ogrn,
            employer_name=_text(company, "name", "company-name", "company_name") or _text(vacancy, "company-name", "company_name"),
            title=title,
            region_code=_text(region, "region_code", "region-code", "code") or _text(address_region, "region_code", "region-code", "code") or _text(vacancy, "region_code", "region-code"),
            region_name=_text(region, "name", "region_name", "region-name") or _text(address_region, "name", "region_name", "region-name"),
            published_at=_moment(vacancy.get("creation-date") or vacancy.get("creation_date") or vacancy.get("published_at") or vacancy.get("date")),
            modified_at=_moment(vacancy.get("date-modified") or vacancy.get("date_modified") or vacancy.get("modified") or vacancy.get("modified_at") or vacancy.get("updated_at") or vacancy.get("change-date")),
            salary_min=_decimal(vacancy.get("salary_min") if vacancy.get("salary_min") is not None else vacancy.get("salary-min")),
            salary_max=_decimal(vacancy.get("salary_max") if vacancy.get("salary_max") is not None else vacancy.get("salary-max")),
            source_url=_text(vacancy, "vac_url", "vac-url", "url"),
        ))
    return result


def total_rows(fetched: Fetched) -> int:
    try:
        payload = json.loads(fetched.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    meta = _mapping(payload.get("meta"))
    try:
        return max(0, int(meta.get("total", 0)))
    except (TypeError, ValueError):
        return 0


def page_url(*, offset: int = 0, limit: int = PAGE_LIMIT, modified_from: str = "") -> str:
    query: dict[str, object] = {"offset": max(0, int(offset)), "limit": min(PAGE_LIMIT, max(1, int(limit)))}
    if modified_from:
        query["modifiedFrom"] = modified_from
    return f"{BASE_URL}?{urlencode(query)}"


def discover(cursor: Cursor | None = None) -> list[RemoteObject]:
    modified_from = cursor.value if cursor and cursor.value else ""
    return [RemoteObject(url=page_url(modified_from=modified_from), kind="vacancies")]


def remaining_pages(fetched: Fetched, *, cursor: Cursor | None = None, limit: int = PAGE_LIMIT) -> list[RemoteObject]:
    total = min(total_rows(fetched), MAX_RESULT_ROWS)
    page_size = min(PAGE_LIMIT, max(1, int(limit)))
    pages = ceil(total / page_size)
    modified_from = cursor.value if cursor and cursor.value else ""
    return [RemoteObject(url=page_url(offset=offset, limit=page_size, modified_from=modified_from), kind="vacancies") for offset in range(1, pages)]


def checkpoint(rows: list[VacancyDatum], *, previous: Cursor | None = None) -> Cursor:
    moments = [row.information_time for row in rows if row.information_time is not None]
    if not moments:
        return previous or Cursor()
    latest = max(moments).astimezone(UTC)
    return Cursor(value=latest.isoformat().replace("+00:00", "Z"), updated_at=latest)


def availability(datum: VacancyDatum, *, first_seen_at: datetime) -> Availability:
    return tradable_at(published_at=datum.information_time, first_seen_at=first_seen_at)


def fetch_plan(permit: Permit, cursor: Cursor | None = None) -> list[RemoteObject]:
    if "fetch" not in permit.operations:
        raise PermissionError(f"{SOURCE_ID}: пропуск не даёт права на загрузку")
    return discover(cursor)


def attribution() -> str:
    return "Источник: открытые данные портала «Работа России», Роструд"


__all__ = ["BASE_URL", "MAX_RESULT_ROWS", "PAGE_LIMIT", "SOURCE_ID", "VacancyDatum", "attribution", "availability", "checkpoint", "discover", "fetch_plan", "page_url", "parse", "remaining_pages", "total_rows"]
