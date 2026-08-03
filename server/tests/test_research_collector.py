"""Сбор: от разрешения до наблюдения в базе (§10.1, §8.2).

До этого модуля контур был собран из частей, но нигде не соединён: реестр
знал, что можно, адаптеры умели разбирать, движки умели считать, а записи
в таблицу наблюдений не делал никто. Экран разведки был пуст не потому,
что рынок спокоен, а потому что цепочка обрывалась.

Здесь проверяется то, что ломается тихо: сбор из закрытого источника,
повторная запись одного факта и время, с которого фактом можно
пользоваться.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.models import ResearchObservation
from app.research import collector
from app.research.sources import sync_registry

NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)

ОТВЕТ_ЦБ = json.dumps(
    {
        "data": [
            {
                "indicator": "Спрос на продукцию",
                "dt": "2026-06-30",
                "value": "3,4",
                "unit": "баланс ответов",
                "industry": "C",
                "published_at": "2026-07-14T09:00:00Z",
            }
        ]
    }
).encode()


class Ответ:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status
        self.headers = {"Content-Type": "application/json"}

    def read(self, _size: int | None = None) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


ДЕРЕВО = json.dumps([
    {"id": 1, "parent_id": -1, "category_name": "Мониторинг предприятий",
     "NoActive": 1},
    {"id": 14, "parent_id": 1, "category_name": "В целом по РФ", "NoActive": 0},
]).encode()


#: Список наборов внутри публикации.
#:
#: Отдельный шаг, а не тот же ответ. Сбор доходил до этого списка и
#: разбирал **его** как данные: у каждой строки есть название показателя и
#: нет ни числа, ни периода — потому что это описание набора, а не факт.
#: В журнале это выглядело как «забрано 11/11, наблюдений 0».
НАБОРЫ = json.dumps([
    {"id": 51, "name": "Спрос на продукцию"},
    {"id": 52, "name": "Ожидания"},
]).encode()


def отдаёт(monkeypatch, body: bytes, наборы: bytes = НАБОРЫ):
    """Сервис трёхшаговый: дерево публикаций → наборы → сами значения."""

    def ответ(request, **_):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if "publications" in url:
            return Ответ(ДЕРЕВО)
        if "datasets" in url:
            return Ответ(наборы)
        return Ответ(body)

    monkeypatch.setattr(collector.urllib.request, "urlopen", ответ)


def test_наблюдение_доходит_до_базы(session, monkeypatch):
    """Ровно то, чего не делал никто: запись факта."""
    sync_registry(session, now=NOW)
    отдаёт(monkeypatch, ОТВЕТ_ЦБ)

    report = collector.collect_cbr(session, now=NOW)
    session.flush()

    assert report.written >= 1
    rows = session.execute(select(ResearchObservation)).scalars().all()
    assert rows
    факт = rows[0]
    assert факт.source_id == "cbr_data"
    assert факт.value_numeric is not None
    # Корень — не источник: у ЦБ наборов несколько, и рождаются они
    # по-разному. Приравняв их, мы получили неверный вывод о недостижимости
    # правила 3–2–1.
    assert факт.source_id == "cbr_data"
    assert факт.lineage_root_id.startswith("cbr:")


def test_повтор_не_плодит_копий(session, monkeypatch):
    """Три копии одного факта в правиле 3–2–1 выглядят как три независимых
    подтверждения — ровно та ошибка, ради которой правило и существует."""
    sync_registry(session, now=NOW)
    отдаёт(monkeypatch, ОТВЕТ_ЦБ)

    первый = collector.collect_cbr(session, now=NOW)
    session.flush()
    второй = collector.collect_cbr(session, now=NOW)
    session.flush()

    assert первый.written >= 1
    assert второй.written == 0
    assert второй.duplicates >= 1


def test_доступность_считается_от_публикации(session, monkeypatch):
    """Показатель за июнь публикуется в июле; использовать его в июне —
    знать будущее."""
    sync_registry(session, now=NOW)
    отдаёт(monkeypatch, ОТВЕТ_ЦБ)

    collector.collect_cbr(session, now=NOW)
    session.flush()

    факт = session.execute(select(ResearchObservation)).scalars().first()
    assert факт.tradable_at.date() > факт.period_end
    assert факт.publication_time_uncertain is False


def test_закрытый_источник_не_собирается_и_называет_причину(session, monkeypatch):
    """Пропуск спрашивается по-настоящему, а не для вида.

    Причина попадает в отчёт: «нельзя по праву» и «сеть не пустила»
    снаружи выглядят одинаково пустым экраном.
    """
    ходил: list[str] = []
    monkeypatch.setattr(
        collector.urllib.request,
        "urlopen",
        lambda *a, **k: ходил.append("сходил") or Ответ(ОТВЕТ_ЦБ),
    )
    # Реестр не синхронизирован — источника в базе нет вовсе.
    report = collector.collect_cbr(session, now=NOW)

    assert report.written == 0
    assert report.skipped and "cbr_data" in report.skipped[0]
    assert not ходил


def test_план_фнс_записывается_как_факт_об_источнике(session, monkeypatch):
    """Какая публикация актуальна сегодня — это факт, и его надо помнить.

    Иначе следующий прогон снова пойдёт читать паспорта, а сравнить
    «изменилась ли публикация» будет не с чем.
    """
    sync_registry(session, now=NOW)
    паспорт = (
        '<a href="https://file.nalog.ru/opendata/7707329152-revexp/'
        'data-20260725-structure-20180110.zip">выгрузка</a>'
        '<a href="/opendata/7707329152-revexp/structure-20180110.xsd">с</a>'
    )
    monkeypatch.setattr(collector, "_text", lambda url: паспорт)

    report = collector.collect_fns(session, now=NOW, catalogue_html=паспорт)
    session.flush()

    assert report.written >= 1
    типы = {
        r.observation_type
        for r in session.execute(select(ResearchObservation)).scalars()
    }
    assert any(t.startswith("fns:plan:") for t in типы)
    факт = session.execute(
        select(ResearchObservation).where(
            ResearchObservation.source_id == "fns_open_data"
        )
    ).scalars().first()
    assert факт.value_text.endswith(".zip")
    assert факт.value_numeric is None


def test_сбор_доходит_до_значений_а_не_до_списка_наборов(session, monkeypatch):
    """Недостающее звено цепочки, найденное по живому логу.

        research: ок — сбор: забрано 11/11, наблюдений 0, повторов 4,
        пропущено 6

    Сбор доходил до списка наборов и разбирал его как данные. У каждой
    строки было название показателя и не было ни числа, ни периода —
    потому что это описание набора, а не факт. Сеть открыта, источник
    отвечает, наблюдений нет.
    """
    sync_registry(session, now=NOW)
    запрошено: list[str] = []

    def ответ(request, **_):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        запрошено.append(url)
        if "publications" in url:
            return Ответ(ДЕРЕВО)
        if "datasets" in url:
            return Ответ(НАБОРЫ)
        return Ответ(ОТВЕТ_ЦБ)

    monkeypatch.setattr(collector.urllib.request, "urlopen", ответ)

    report = collector.collect_cbr(session, now=NOW)
    session.flush()

    данные = [u for u in запрошено if "/data?" in u]
    assert данные, "к самим значениям запрос так и не ушёл"
    # Идентификатор набора берётся из ответа, а не из нашего имени:
    # нумерация — собственность сервиса.
    assert "datasetId=51" in данные[0]
    assert report.written >= 1


def test_непонятый_ответ_называет_форму_а_не_молчит(session, monkeypatch):
    """«Разобрано 0 строк» не отвечает на вопрос почему.

    Контракт источника меняется, и следующий шаг должен делаться по факту
    ответа, а не по догадке о нём. В отчёт идут имена полей, не значения:
    журнал — не место для выгрузки чужих данных.
    """
    sync_registry(session, now=NOW)
    чужая_форма = json.dumps({"payload": [{"someField": 1, "otherField": 2}]}).encode()
    отдаёт(monkeypatch, чужая_форма)

    report = collector.collect_cbr(session, now=NOW)

    assert report.written == 0
    assert any("someField" in e for e in report.errors), report.errors
    assert not any("1" == e for e in report.errors)


def test_значение_называется_так_как_его_зовёт_сервис(session, monkeypatch):
    """`obs_val` — имя значения в ответе метода данных.

    Прежний разбор его не знал, потому что и до метода не доходил.
    """
    sync_registry(session, now=NOW)
    ответ_данных = json.dumps({
        "RawData": [
            {"elname": "Спрос", "obs_val": "3.4", "year": 2026, "month": 6},
        ]
    }).encode()
    отдаёт(monkeypatch, ответ_данных)

    report = collector.collect_cbr(session, now=NOW)
    session.flush()

    assert report.written >= 1
    факт = session.execute(select(ResearchObservation)).scalars().first()
    assert факт.value_numeric is not None
    # Период собран из года и месяца по отдельности: без сборки наблюдение
    # выглядит вневременным и в правиле 3–2–1 не считается ни за какой период.
    assert факт.period_end is not None
    assert факт.period_end.year == 2026


def test_отчёт_называет_числа_а_не_настроение():
    """Строка журнала — единственное место, где видно, идёт ли сбор."""
    report = collector.CollectReport(attempted=4, fetched=3, written=120, duplicates=7)
    текст = report.summary()
    assert "3/4" in текст
    assert "120" in текст
    assert "повторов 7" in текст
