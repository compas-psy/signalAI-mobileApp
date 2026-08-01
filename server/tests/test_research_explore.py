"""Разведка каталога: находить выгрузки, а не сочинять их адреса (§10.1).

Живой прогон показал, чем кончается конструирование адреса из имени
набора: ЦБ ответил 501 на все четыре, ФНС 404 на все четыре. Разбор при
этом верный и fixtures проходят — то есть тесты были зелёными ровно в тот
момент, когда собирать было нечего.

Отсюда правило, которое эти тесты и держат: адрес выгрузки приходит из
каталога, а сам каталог — из реестра.
"""

from __future__ import annotations

import io

from app.research import explore
from app.research.sources import ALL_SOURCES

СТРАНИЦА = """
<html><body>
  <a href="/opendata/7707329152-revexp/">Доходы и расходы</a>
  <a href="/about/">О ФНС</a>
  <a href="https://file.nalog.ru/opendata/7707329152-revexp/data-20260401.zip">выгрузка</a>
  <a href="https://vk.com/nalog">мы в соцсетях</a>
</body></html>
""".encode()


class Ответ:
    def __init__(self, body: bytes, status: int = 200, ctype: str = "text/html"):
        self._stream = io.BytesIO(body)
        self.status = status
        self.headers = {"Content-Type": ctype}

    def read(self, size: int | None = None) -> bytes:
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def открыватель(response):
    return lambda _request, **__: response


def test_ссылки_на_выгрузки_отделяются_от_меню():
    """Каталог ведомства — это сотня пунктов навигации и десяток данных."""
    found = explore.fetch("https://www.nalog.gov.ru/opendata/",
                          opener=открыватель(Ответ(СТРАНИЦА)))

    assert any(url.endswith(".zip") for url in found.matched)
    assert any("7707329152-revexp" in url for url in found.matched)
    assert not any("vk.com" in url for url in found.matched)
    assert not any("/about/" in url for url in found.matched)


def test_относительная_ссылка_становится_полной():
    """Половина каталогов пишет относительные пути, и склеивать их руками
    в адаптере — верный способ получить 404."""
    found = explore.fetch("https://www.nalog.gov.ru/opendata/",
                          opener=открыватель(Ответ(СТРАНИЦА)))
    assert "https://www.nalog.gov.ru/opendata/7707329152-revexp/" in found.matched


def test_машинный_ответ_тоже_отдаёт_адреса():
    """Каталог бывает JSON-ом; HTML-парсер на нём находит ноль ссылок.

    Пустой список выглядел бы как «в каталоге ничего нет».
    """
    payload = (
        b'{"datasets":[{"url":"https://www.cbr.ru/dataservice/data/12"}]}'
    )
    found = explore.fetch(
        "https://www.cbr.ru/dataservice/datasets",
        opener=открыватель(Ответ(payload, ctype="application/json")),
    )
    assert "https://www.cbr.ru/dataservice/data/12" in found.matched
    assert "dataservice" in found.head


def test_ошибка_каталога_называется_а_не_прячется():
    import urllib.error

    def падает(_request, **__):
        raise urllib.error.HTTPError("u", 501, "Not Implemented", {}, None)

    found = explore.fetch("https://www.cbr.ru/dataservice/data", opener=падает)
    assert found.status == 501
    assert "501" in found.error
    assert found.matched == []


def test_тело_обрезается():
    """Каталог — это список, а не выгрузка."""
    огромный = b"<a href='https://x.test/a.zip'>x</a>" + b"." * (explore.MAX_BYTES * 2)
    found = explore.fetch("https://x.test/", opener=открыватель(Ответ(огромный)))
    assert found.size <= explore.MAX_BYTES


def test_адреса_каталогов_объявлены_в_реестре():
    """Произвольную ссылку разведке передать нельзя.

    Иначе диагностика стала бы способом ходить в сеть с сервера владельца.
    """
    объявлено = {
        url for spec in ALL_SOURCES for url in spec.catalogue_urls
    }
    assert объявлено
    for url in объявлено:
        assert url.startswith("https://"), url


def test_у_обоих_написанных_адаптеров_есть_точка_входа():
    """Адаптер без каталога может только сочинять адреса."""
    with_catalogue = {s.source_id for s in ALL_SOURCES if s.catalogue_urls}
    assert {"cbr_data", "fns_open_data"} <= with_catalogue
