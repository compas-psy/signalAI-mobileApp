"""Адаптеры ЦБ и ФНС на зафиксированных выгрузках (§10.3, §25.4).

Fixtures построены по опубликованным контрактам: OpenAPI сервиса данных ЦБ
и XSD открытых наборов ФНС. Живой прогон делается после деплоя — из
контейнера разработки оба хоста недоступны, и делать вид, что адаптер
проверен по сети, нельзя.

Проверяется то, что ломается на настоящих данных: пустое поле вместо нуля,
изменившееся имя тега, архив вместо файла, и главное — что время
публикации не выводится из периода.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.research.adapters import cbr, fns
from app.research.collect import Fetched
from app.research.policy import Permit
from tests.conftest import DEVICE_HEADERS

D = Decimal
NOW = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)


def fetched(body: bytes) -> Fetched:
    return Fetched(
        url="https://example.test/data",
        status=200,
        body=body,
        requested_at=NOW,
        responded_at=NOW,
    )


def permit(*operations: str) -> Permit:
    return Permit(
        source_id="test",
        operations=frozenset(operations),
        issued_at=NOW,
        expires_at=NOW,
    )


# ── Банк России ─────────────────────────────────────────────────────────────

CBR_PAYLOAD = json.dumps(
    {
        "data": [
            {
                "indicator": "Спрос на продукцию",
                "dt": "2026-07-31",
                "value": "3,4",
                "unit": "баланс ответов",
                "industry": "C",
                "published_at": "2026-08-14T09:00:00Z",
            },
            {
                "indicator": "Спрос на продукцию",
                "dt": "2026-06-30",
                "value": "",
                "industry": "C",
                "published_at": "2026-07-14T09:00:00Z",
            },
        ]
    }
).encode()


def test_разбор_отдаёт_значения_с_периодом_и_публикацией():
    rows = cbr.parse(fetched(CBR_PAYLOAD), dataset="enterprise_monitoring")

    assert len(rows) == 2
    first = rows[0]
    assert first.value == D("3.4")
    assert first.period_end == date(2026, 7, 31)
    assert first.published_at == datetime(2026, 8, 14, 9, 0, tzinfo=UTC)


def test_пустое_значение_это_не_ноль():
    """«Не измерено» и «ноль» — разные факты, и путать их нельзя."""
    rows = cbr.parse(fetched(CBR_PAYLOAD), dataset="enterprise_monitoring")
    assert rows[1].value is None
    assert not rows[1].measured


def test_запятая_как_разделитель_читается():
    rows = cbr.parse(fetched(CBR_PAYLOAD), dataset="enterprise_monitoring")
    assert rows[0].value == D("3.4")


def test_доступность_считается_от_публикации_а_не_от_периода():
    """Показатель за июль существует с конца июля, а узнать о нём можно в августе.

    Проверка, использующая его в июле, знает будущее.
    """
    rows = cbr.parse(fetched(CBR_PAYLOAD), dataset="enterprise_monitoring")
    when = cbr.availability(rows[0], first_seen_at=NOW)
    assert when.tradable_at.date() > date(2026, 7, 31)
    assert when.tradable_at.date() >= date(2026, 8, 14)


def test_битый_ответ_не_роняет_разбор():
    """Адаптер, падающий на новом поле, останавливает сбор целиком."""
    assert cbr.parse(fetched("<html>ошибка</html>".encode()), dataset="key_rate") == []


def test_неизвестное_поле_не_ломает_запись():
    payload = json.dumps(
        {"data": [{"indicator": "X", "dt": "2026-07-31", "value": "1",
                   "новое_поле": "что-то"}]}
    ).encode()
    rows = cbr.parse(fetched(payload), dataset="key_rate")
    assert rows[0].value == D("1")


def test_пропуск_без_права_загрузки_не_даёт_плана():
    """Адаптер не может сходить в сеть без пропуска — это сигнатура."""
    with pytest.raises(PermissionError):
        cbr.fetch_plan(permit("store_raw"))

    assert cbr.fetch_plan(permit("fetch", "store_raw"))


# ── ФНС ─────────────────────────────────────────────────────────────────────

FNS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Файл>
  <Документ ИННЮЛ="7736050003">
    <СвНП СумДоход="1250000.50" СумРасход="900000.25"/>
  </Документ>
  <Документ ИННЮЛ="7707083893">
    <СвНП СумДоход="" СумРасход=""/>
  </Документ>
  <Документ>
    <СвНП СумДоход="999"/>
  </Документ>
</Файл>
""".encode()

FNS_HEADCOUNT = """<?xml version="1.0" encoding="UTF-8"?>
<Файл>
  <Документ>
    <ИНН>7736050003</ИНН>
    <КолРаб>15400</КолРаб>
  </Документ>
</Файл>
""".encode()


def test_разбор_выгрузки_даёт_показатели_по_инн():
    rows = fns.parse(
        fetched(FNS_XML), dataset="revenue_expenses", period_end=date(2025, 12, 31)
    )
    by_inn = {r.inn: r for r in rows}

    assert by_inn["7736050003"].value == D("1250000.50")
    assert by_inn["7736050003"].secondary_value == D("900000.25")


def test_пустое_поле_означает_не_раскрыто():
    """Компания без задолженности и компания, о которой не сообщили, — разное."""
    rows = fns.parse(
        fetched(FNS_XML), dataset="revenue_expenses", period_end=date(2025, 12, 31)
    )
    assert "7707083893" not in {r.inn for r in rows}


def test_запись_без_инн_отбрасывается():
    """Показатель, который не к кому отнести, наблюдением не является."""
    rows = fns.parse(
        fetched(FNS_XML), dataset="revenue_expenses", period_end=date(2025, 12, 31)
    )
    assert len(rows) == 1


def test_имя_поля_читается_и_из_атрибута_и_из_элемента():
    """ФНС использует оба способа в разных наборах, и падать на этом нельзя."""
    rows = fns.parse(
        fetched(FNS_HEADCOUNT), dataset="headcount", period_end=date(2025, 12, 31)
    )
    assert rows[0].inn == "7736050003"
    assert rows[0].value == D("15400")
    assert rows[0].unit == "people"


def test_архив_разбирается_как_и_файл():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("part-1.xml", FNS_XML)
        archive.writestr("part-2.xml", FNS_HEADCOUNT)
        archive.writestr("readme.txt", "не xml")

    rows = fns.parse(
        fetched(buffer.getvalue()),
        dataset="revenue_expenses",
        period_end=date(2025, 12, 31),
    )
    assert any(r.inn == "7736050003" for r in rows)


def test_годовые_данные_не_доступны_с_первого_января():
    """Набор за 2025 год публикуется в 2026-м, и разрыв бывает больше года."""
    published = datetime(2026, 10, 1, tzinfo=UTC)
    when = fns.availability(published_at=published, first_seen_at=NOW)
    assert when.tradable_at > published
    assert when.tradable_at.year == 2026
    assert when.tradable_at.month >= 10


def test_отсутствие_времени_публикации_помечается():
    when = fns.availability(published_at=None, first_seen_at=NOW)
    assert when.publication_time_uncertain


def test_четыре_набора_с_разной_частотой():
    """Слить их в один источник — потерять точечность у трёх из четырёх."""
    assert set(fns.DATASETS) == {
        "revenue_expenses",
        "headcount",
        "paid_taxes",
        "tax_debt",
    }
    assert fns.DATASETS["tax_debt"] == "quarterly"
    assert fns.DATASETS["revenue_expenses"] == "yearly"


def test_ссылка_на_источник_обязательна():
    """Одно из трёх условий использования открытых данных ФНС."""
    assert "налогов" in fns.attribution().lower()


def test_пропуск_проверяется_и_у_фнс():
    with pytest.raises(PermissionError):
        fns.fetch_plan(permit("transform"))
    assert len(fns.fetch_plan(permit("fetch"))) == 4


def test_проба_доступности_не_ходит_в_сеть_у_запрещённого_источника(session):
    """Пробовать достучаться туда, куда нельзя, — то же нарушение, что и сбор.

    И одновременно это разделяет две причины пустого экрана: «нельзя по
    праву» и «не пускает сеть».
    """
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app
    from app.research.sources import sync_registry

    sync_registry(session, now=NOW)
    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app, headers=DEVICE_HEADERS) as client:
            body = client.get("/api/v1/research/sources/reachability").json()
    finally:
        app.dependency_overrides.clear()

    by_id = {row["source_id"]: row for row in body}
    assert "moex_iss" in by_id
    assert by_id["moex_iss"]["reachable"] is False
    assert "проба не отправлялась" in by_id["moex_iss"]["detail"]
    # А у разрешённого проба уходит и приносит причину, если сеть закрыта.
    assert "cbr_data" in by_id
    assert "проба не отправлялась" not in by_id["cbr_data"]["detail"]
    # Проверяется именно точка входа в каталог, а не корень сервиса.
    # Корень `/dataservice/` отвечает 404, и это ничего не значит — сервис
    # состоит из именованных точек входа, — но панель показывала именно его,
    # и полностью живой источник читался как сломанный.
    assert by_id["cbr_data"]["hosts"]
    assert "publications" in by_id["cbr_data"]["hosts"][0]["url"]
