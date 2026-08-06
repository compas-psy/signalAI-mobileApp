"""Проба доступности: за что она отвечает и за что не отвечает.

Ошибка здесь дороже, чем кажется. Проба — единственное, что владелец
читает после деплоя, решая, идти ли править брандмауэр. Сказать «сеть
закрыта» про молчащий сервер значит отправить человека настраивать то,
что и так работает, и оставить настоящую причину неизвестной.

Сеть проверяется соединением, источник — запросом. Тесты держат эту
границу.
"""

from __future__ import annotations

import socket
import ssl
from datetime import UTC, datetime

import pytest

from app.research import reach
from app.research.sources import ALL_SOURCES
from tests.conftest import DEVICE_HEADERS

NOW = datetime(2026, 8, 1, tzinfo=UTC)


class FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def открытая_сеть(monkeypatch):
    """Соединение и рукопожатие проходят; что дальше — дело источника."""
    monkeypatch.setattr(
        reach.socket, "getaddrinfo", lambda *a, **k: [("af", "sk", 6, "", ("1.2.3.4", 443))]
    )
    monkeypatch.setattr(
        reach.socket, "create_connection", lambda *a, **k: FakeSocket()
    )

    class Context:
        def wrap_socket(self, *_, **__):
            return FakeSocket()

    monkeypatch.setattr(reach.ssl, "create_default_context", Context)


def test_молчание_источника_при_открытой_сети_не_называется_блокировкой(
    открытая_сеть, monkeypatch
):
    """Read timeout приходит после установленного TLS — сеть уже выпустила.

    Именно так повёл себя file.nalog.ru на первом живом прогоне, и старая
    проба отвечала «Проверьте исходящий HTTPS».
    """

    def hang(*_, **__):
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(reach.urllib.request, "urlopen", hang)

    result = reach.probe("https://file.nalog.ru/opendata/")

    assert result.network_open is True
    assert result.answered is False
    assert result.blocked_by_network is False
    assert "исходящ" not in result.detail.lower()
    assert "Брандмауэр здесь ни при чём" in result.detail


def test_закрытый_порт_называется_сетью_и_называет_хост(monkeypatch):
    """Здесь совет про исходящий доступ уместен — и обязан быть конкретным."""
    monkeypatch.setattr(
        reach.socket, "getaddrinfo", lambda *a, **k: [("af", "sk", 6, "", ("1.2.3.4", 443))]
    )

    def refuse(*_, **__):
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(reach.socket, "create_connection", refuse)

    result = reach.probe("https://www.cbr.ru/dataservice/")

    assert result.blocked_by_network is True
    assert "www.cbr.ru:443" in result.detail


def test_неразрешимое_имя_это_не_брандмауэр(monkeypatch):
    """Правило для исходящих не чинит DNS, и путать причины нельзя."""

    def fail(*_, **__):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(reach.socket, "getaddrinfo", fail)

    result = reach.probe("https://нет-такого.test/")
    assert result.network_open is False
    assert "не разрешается" in result.detail


def test_ошибка_сертификата_означает_что_сеть_пустила(monkeypatch):
    """Спор о доверии идёт уже с хостом: пакеты дошли."""
    monkeypatch.setattr(
        reach.socket, "getaddrinfo", lambda *a, **k: [("af", "sk", 6, "", ("1.2.3.4", 443))]
    )
    monkeypatch.setattr(reach.socket, "create_connection", lambda *a, **k: FakeSocket())

    class Context:
        def wrap_socket(self, *_, **__):
            raise ssl.SSLCertVerificationError("certificate verify failed")

    monkeypatch.setattr(reach.ssl, "create_default_context", Context)
    monkeypatch.setattr(
        reach.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("нет ответа")),
    )

    result = reach.probe("https://www.cbr.ru/dataservice/")
    assert result.network_open is True


def test_любой_код_ответа_считается_ответом(открытая_сеть, monkeypatch):
    """404 на корне каталога — ответ, а не недоступность."""
    import urllib.error

    def not_found(*_, **__):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    monkeypatch.setattr(reach.urllib.request, "urlopen", not_found)

    result = reach.probe("https://www.cbr.ru/dataservice/")
    assert result.network_open is True
    assert result.answered is True
    assert result.status == 404


def test_запрос_идёт_get_ом_и_представляется(открытая_сеть, monkeypatch):
    """HEAD часть государственных файловых хостов не отвечает вовсе.

    А ссылка на пользователя данных — условие использования наборов ФНС,
    и анонимная проба ему противоречит.
    """
    captured = {}

    class Response:
        status = 206

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def capture(request, **__):
        captured["method"] = request.get_method()
        captured["agent"] = request.get_header("User-agent")
        captured["range"] = request.get_header("Range")
        return Response()

    monkeypatch.setattr(reach.urllib.request, "urlopen", capture)

    reach.probe("https://file.nalog.ru/opendata/")

    assert captured["method"] == "GET"
    assert "SignalAI" in captured["agent"]
    assert captured["range"] == "bytes=0-0"


# ── Все хосты источника, а не только базовый ────────────────────────────────


def test_у_фнс_проверяются_все_три_хоста():
    """Файлы, описания наборов и структура лежат на разных хостах.

    Открытый доступ к одному ничего не говорит о двух других, а владелец
    открывает доступ списком — и должен видеть ответ по всему списку.
    """
    spec = next(s for s in ALL_SOURCES if s.source_id == "fns_open_data")
    hosts = {
        u.split("//")[-1].split("/")[0] for u in (spec.base_url, *spec.extra_urls)
    }
    assert hosts == {"file.nalog.ru", "www.nalog.gov.ru", "data.nalog.ru"}


def test_каждый_дополнительный_адрес_это_https():
    """Проба по http выглядела бы успешной там, где закрыт именно HTTPS."""
    for spec in ALL_SOURCES:
        for url in spec.extra_urls:
            assert url.startswith("https://"), spec.source_id


# ── Проверка плана сбора ────────────────────────────────────────────────────


def test_у_каждого_адаптера_есть_источник_в_реестре():
    """Адаптер без записи в реестре собирать не сможет никогда.

    Связь держится строкой `SOURCE_ID` внутри модуля, и опечатка в ней
    выглядит как «источник просто ничего не приносит».
    """
    from app.research.adapters import BY_SOURCE

    known = {s.source_id for s in ALL_SOURCES}
    assert set(BY_SOURCE) <= known


def test_план_проверяется_только_по_ссылкам_адаптера(session, monkeypatch):
    """Через этот адрес нельзя сходить по произвольной ссылке.

    Иначе диагностика превратилась бы в способ ходить в сеть чужими
    руками — с сервера владельца и в обход правового шлюза.
    """
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app
    from app.research.adapters import fns
    from app.research.sources import sync_registry

    посещённые: list[str] = []

    def записать(url, **_):
        посещённые.append(url)
        return reach.HostProbe(
            host=url.split("/")[2], url=url, network_open=True,
            answered=True, status=200, detail="источник отвечает",
        )

    monkeypatch.setattr("app.api.v1.research.reach.probe", записать)
    sync_registry(session, now=NOW)
    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app, headers=DEVICE_HEADERS) as client:
            body = client.get(
                "/api/v1/research/sources/plan-check?url=https://зло.test/"
            ).json()
    finally:
        app.dependency_overrides.clear()

    ожидаемые = {o.url for o in fns.discover()}
    assert ожидаемые <= set(посещённые)
    assert not any("зло.test" in url for url in посещённые)
    assert {row["source_id"] for row in body} <= {"cbr_data", "fns_open_data"}


def test_закрытый_по_праву_источник_в_плане_не_проверяется(session, monkeypatch):
    """Пропуск запрашивается по-настоящему, и отказ виден в ответе."""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app
    from app.research.sources import sync_registry

    monkeypatch.setattr(
        "app.api.v1.research.reach.probe",
        lambda url, **_: reach.HostProbe(
            host="", url=url, network_open=True, answered=True,
            status=200, detail="ок",
        ),
    )
    # Пропуск для ЦБ не выдаётся, если реестр не синхронизирован.
    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app, headers=DEVICE_HEADERS) as client:
            body = client.get("/api/v1/research/sources/plan-check").json()
    finally:
        app.dependency_overrides.clear()

    for row in body:
        assert row["url"] == ""
        assert "проба не отправлялась" in row["detail"]
