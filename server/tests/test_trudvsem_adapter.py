"""Контракт официального API «Работа России» для HIRING (§10, §13.3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.research.adapters import trudvsem
from app.research.collect import Cursor, Fetched
from app.research.policy import Permit

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def fetched(payload: dict) -> Fetched:
    return Fetched(
        url="https://opendata.trudvsem.ru/api/v1/vacancies?offset=0&limit=100",
        status=200,
        body=json.dumps(payload, ensure_ascii=False).encode(),
        requested_at=NOW,
        responded_at=NOW,
    )


def permit(*operations: str) -> Permit:
    return Permit(
        source_id=trudvsem.SOURCE_ID,
        operations=frozenset(operations),
        issued_at=NOW,
        expires_at=NOW,
    )


PAYLOAD = {
    "status": "200",
    "meta": {"total": "205"},
    "results": {
        "vacancies": [
            {
                "vacancy": {
                    "id": "vac-1",
                    "job-name": "Инженер по автоматизации",
                    "company": {
                        "companycode": "company-7",
                        "name": "ПАО Тест",
                        "inn": "7700000001",
                        "ogrn": "1027700000001",
                    },
                    "region": {"region_code": "7700000000000", "name": "Москва"},
                    "creation-date": "2026-08-01T07:30:00Z",
                    "date-modified": "2026-08-05T09:45:00Z",
                    "salary_min": "180 000,00",
                    "salary_max": "240000",
                    "vac_url": "https://trudvsem.ru/vacancy/vac-1",
                    "contact-list": {"email": "person@example.test", "phone": "+7"},
                    "new-field-from-source": {"anything": True},
                }
            },
            {
                "vacancy": {
                    "id": "vac-2",
                    "job-name": "Специалист по закупкам",
                    "companycode": "company-7",
                    "company-name": "ПАО Тест",
                    "region_code": "7800000000000",
                    "creation_date": "2026-08-04T10:00:00+03:00",
                    "salary_min": "",
                    "salary_max": None,
                }
            },
            {"vacancy": {"id": "vac-3", "job-name": "Продавец"}},
        ]
    },
}


def test_разбирает_вакансию_с_официальными_идентификаторами():
    rows = trudvsem.parse(fetched(PAYLOAD))
    assert len(rows) == 2
    first = rows[0]
    assert first.vacancy_id == "vac-1"
    assert first.employer_identity == "7700000001"
    assert first.title == "Инженер по автоматизации"
    assert first.region_name == "Москва"
    assert first.salary_min == Decimal("180000.00")
    assert first.salary_max == Decimal("240000")
    assert first.salary_mid == Decimal("210000.00")
    assert first.modified_at == datetime(2026, 8, 5, 9, 45, tzinfo=UTC)


def test_пустая_зарплата_не_становится_нулём():
    rows = trudvsem.parse(fetched(PAYLOAD))
    second = rows[1]
    assert second.salary_min is None
    assert second.salary_max is None
    assert second.salary_mid is None


def test_контакты_не_попадают_в_каноническую_модель():
    row = trudvsem.parse(fetched(PAYLOAD))[0]
    assert not hasattr(row, "email")
    assert not hasattr(row, "phone")
    assert "person@example.test" not in repr(row)


def test_битый_или_ошибочный_ответ_даёт_пустой_набор():
    broken = Fetched(
        url="https://example.test",
        status=200,
        body=b"<html>error</html>",
        requested_at=NOW,
        responded_at=NOW,
    )
    denied = fetched({"status": "500", "meta": {"error": "temporary"}})
    assert trudvsem.parse(broken) == []
    assert trudvsem.parse(denied) == []


def test_инкрементальный_курсор_попадает_в_modified_from():
    cursor = Cursor(value="2026-08-01T00:00:00Z", updated_at=NOW)
    plan = trudvsem.discover(cursor)
    assert len(plan) == 1
    assert "modifiedFrom=2026-08-01T00%3A00%3A00Z" in plan[0].url
    assert "offset=0" in plan[0].url
    assert "limit=100" in plan[0].url


def test_курсор_двигается_по_официальному_времени_изменения():
    rows = trudvsem.parse(fetched(PAYLOAD))
    cursor = trudvsem.checkpoint(rows)
    assert cursor.value == "2026-08-05T09:45:00Z"
    assert cursor.updated_at == datetime(2026, 8, 5, 9, 45, tzinfo=UTC)


def test_пагинация_учитывает_total_и_предел_источника():
    first = fetched({**PAYLOAD, "meta": {"total": "205"}})
    pages = trudvsem.remaining_pages(first)
    assert len(pages) == 2
    assert "offset=1" in pages[0].url
    assert "offset=2" in pages[1].url

    huge = fetched({**PAYLOAD, "meta": {"total": "50000"}})
    capped = trudvsem.remaining_pages(huge)
    assert len(capped) == 99
    assert "offset=99" in capped[-1].url


def test_доступность_считается_от_изменения_а_не_от_даты_роли():
    row = trudvsem.parse(fetched(PAYLOAD))[0]
    when = trudvsem.availability(row, first_seen_at=NOW)
    assert when.tradable_at > NOW
    assert not when.publication_time_uncertain


def test_без_права_fetch_план_не_выдаётся():
    with pytest.raises(PermissionError):
        trudvsem.fetch_plan(permit("store_raw"))
    assert trudvsem.fetch_plan(permit("fetch"))


def test_атрибуция_называет_официальный_источник():
    assert "Работа России" in trudvsem.attribution()
    assert "Роструд" in trudvsem.attribution()
