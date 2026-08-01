"""Диагностика молчащего источника: фаза важнее факта (§10.1, §25.4).

`file.nalog.ru` принимает TLS и не отвечает по HTTP. Одинаково выглядят
четыре разные причины: фильтрация маршрута, сломанный IPv6, неподдержанный
`Range` и слишком большая выгрузка. Обычная проба их не различает, и
решение по ней принять нельзя.

Здесь проверяется, что различает: каждая фаза называется отдельно, и
вывод «сеть закрыта» не делается там, где сеть уже сработала.
"""

from __future__ import annotations

import socket

import pytest

from app.research import netdiag
from app.research.adapters import cbr, fns

ПАСПОРТ = """
<html><body><table>
<tr><td>Гиперссылка на набор</td><td>
  <a href="https://file.nalog.ru/opendata/7707329152-revexp/data-20260725-structure-20180110.zip">скачать</a>
</td></tr>
<tr><td>Гиперссылка на структуру</td><td>
  <a href="/opendata/7707329152-revexp/structure-20180110.xsd">структура</a>
</td></tr>
</table></body></html>
"""


# ── Ссылки берутся из паспорта, а не из шаблона ─────────────────────────────


def test_адреса_выгрузок_читаются_из_паспорта():
    """Имя файла содержит дату выпуска и дату версии структуры.

    Собрать такое по шаблону нельзя: живой прогон дал 404 по всем четырём
    выдуманным именам, и выглядело это как спокойный рынок.
    """
    links = fns.links_from_passport(ПАСПОРТ, fns.PASSPORTS["revenue_expenses"])
    assert links["data"].endswith("data-20260725-structure-20180110.zip")
    assert links["structure"].endswith("structure-20180110.xsd")


def test_разбор_не_привязан_к_номеру_строки():
    """Номер строки — свойство вёрстки, расширение — свойство файла.

    Ведомство переставит поля местами, и разбор по восьмой строке начнёт
    молча брать не то.
    """
    перевёрнутый = "\n".join(reversed(ПАСПОРТ.splitlines()))
    links = fns.links_from_passport(перевёрнутый, fns.PASSPORTS["revenue_expenses"])
    assert links["data"].endswith(".zip")
    assert links["structure"].endswith(".xsd")


def test_без_паспорта_план_ведёт_к_паспортам_а_не_к_выдумке():
    """Обещать план, который не сработает, хуже, чем признать незнание."""
    план = fns.discover()
    assert all(o.kind.endswith(":passport") for o in план)
    assert all("nalog.gov.ru" in o.url for o in план)


def test_структура_проверяется_раньше_выгрузки():
    """XSD маленький: если молчит он, дело не в размере выгрузки."""
    план = fns.discover(passports={"revenue_expenses": ПАСПОРТ})
    свои = [o for o in план if o.kind.startswith("revenue_expenses")]
    assert свои[0].kind.endswith(":structure")
    assert свои[1].kind == "revenue_expenses"


def test_паспорта_находятся_в_каталоге_а_не_угадываются():
    """Год в имени набора меняется: sshr стал sshr2019.

    Жёсткое имя ломается молча — набор просто перестаёт находиться.
    """
    каталог = (
        '<a href="/opendata/7707329152-revexp">1</a>'
        '<a href="/opendata/7707329152-sshr2019">2</a>'
        '<a href="/opendata/7707329152-paytax">3</a>'
        '<a href="/opendata/7707329152-debtam">4</a>'
        '<a href="/opendata/7707329152-fias">лишний</a>'
    )
    found = fns.passports_from_catalogue(каталог)
    assert found["headcount"].endswith("sshr2019")
    assert "fias" not in " ".join(found.values())


def test_публикации_цб_делятся_на_разделы_и_данные():
    """`NoActive: 1` — узел-раздел, забирать из него нечего.

    Снаружи раздел и лист выглядят одинаково публикацией.
    """
    payload = b'[{"id":1,"NoActive":1},{"id":14,"NoActive":0}]'
    assert [p["id"] for p in cbr.leaf_publications(payload)] == [14]


def test_идентификатор_публикации_числовой():
    """У сервиса своя нумерация, наше имя набора туда не подставляется."""
    assert cbr.datasets_of(14).endswith("datasets?publicationId=14")


def test_цб_начинается_с_публикаций_а_не_с_корня():
    """Корень отвечает 404, и это ничего не значит: сервис — набор методов.

    Собранный `/dataservice/data?dataset=...` отвечал 501 на все четыре.
    """
    первый = cbr.discover()[0]
    assert первый.url.endswith("/dataservice/publications")
    assert not any("data?dataset=" in o.url for o in cbr.discover())


def test_html_вместо_json_это_ноль_публикаций_а_не_пустой_список():
    """Сервис на неверный метод отвечает страницей с кодом 200.

    Прочитанная как список наборов, она даёт пустоту — неотличимую от
    «наборов нет».
    """
    assert cbr.publications("<html>ошибка</html>".encode()) == []
    assert cbr.publications(b'{"publications":[{"id":1}]}') == [{"id": 1}]


# ── Фазы ────────────────────────────────────────────────────────────────────


def test_имя_не_разрешается_это_фаза_dns(monkeypatch):
    monkeypatch.setattr(
        netdiag.socket, "getaddrinfo",
        lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("нет такого")),
    )
    detail = netdiag.probe("https://нет.test/")
    assert detail.phase == "dns"
    assert detail.status is None


def test_молчание_после_соединения_это_фаза_ttfb(monkeypatch):
    """Ровно то, что делает file.nalog.ru: соединение есть, ответа нет."""

    class Соединение:
        sock = type("s", (), {"getpeername": lambda self: ("1.2.3.4", 443)})()

        def connect(self):
            return None

        def request(self, *_, **__):
            raise TimeoutError("timed out")

        def close(self):
            return None

    monkeypatch.setattr(
        netdiag.socket, "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("1.2.3.4", 443))],
    )
    monkeypatch.setattr(netdiag, "_connection", lambda *a, **k: Соединение())

    detail = netdiag.probe("https://file.nalog.ru/opendata/x.zip")
    assert detail.phase == "ttfb"
    assert detail.resolved_ip == "1.2.3.4"
    assert detail.connect_ms is not None
    # Соединение состоялось — про брандмауэр здесь нечего сказать.
    assert "исходящ" not in detail.error.lower()


def test_заголовки_есть_тело_молчит_это_фаза_body(monkeypatch):
    """Отдельная причина: так ведёт себя большая выгрузка, а не фильтр."""

    class Ответ:
        status = 200

        def getheader(self, name, default=""):
            return {"Content-Type": "application/octet-stream"}.get(name, default)

        def read(self, _size):
            raise TimeoutError("timed out")

    class Соединение:
        sock = type("s", (), {"getpeername": lambda self: ("1.2.3.4", 443)})()

        def connect(self):
            return None

        def request(self, *_, **__):
            return None

        def getresponse(self):
            return Ответ()

        def close(self):
            return None

    monkeypatch.setattr(
        netdiag.socket, "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("1.2.3.4", 443))],
    )
    monkeypatch.setattr(netdiag, "_connection", lambda *a, **k: Соединение())

    detail = netdiag.probe("https://file.nalog.ru/opendata/x.zip")
    assert detail.phase == "body"
    assert detail.status == 200
    assert detail.ttfb_ms is not None


def test_версия_протокола_причиной_быть_не_может():
    """`http.client` говорит только по HTTP/1.1 — вторая версия не в игре."""
    assert netdiag.Detail(url="u").http_version == "HTTP/1.1"


def test_принудительный_ipv4_возвращает_резолвер_на_место():
    """Подмена глобального резолвера, которая пережила бы запрос, — авария."""
    было = socket.getaddrinfo
    with netdiag._only_ipv4():
        assert socket.getaddrinfo is not было
    assert socket.getaddrinfo is было


def test_диапазон_не_основная_проба():
    """Файловые серверы обрабатывают частичный запрос по-разному.

    Молчание из-за неподдержанного заголовка неотличимо от молчания
    вообще, поэтому первая проба идёт без него.
    """
    assert netdiag.Detail(url="u").used_range is False
    with pytest.raises(TypeError):
        netdiag.probe("https://x.test/", "bytes=0-0")  # только именованным
