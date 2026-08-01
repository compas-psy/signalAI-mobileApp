"""Разведка каталога источника: что там лежит на самом деле (ТЗ §10.1).

Зачем это существует. Адаптеры собирали адреса из имени набора — и живой
прогон показал результат: ЦБ отвечает 501 на все четыре, ФНС 404 на все
четыре. Разбор при этом верный, fixtures проходят, тесты зелёные. То есть
сбор не пошёл бы, а выглядело бы это как спокойный рынок: экран пуст,
ошибок нет, объяснить нечем.

Конструировать адрес из имени набора нельзя в принципе. Государственные
каталоги нумеруют наборы своими идентификаторами, добавляют дату выпуска в
имя файла и меняют раскладку между годами. Единственный надёжный способ —
прийти в каталог и прочитать, что в нём есть.

Модуль намеренно не «универсальный парсер сайта». Он делает ровно одно:
приносит содержимое объявленной в реестре точки входа и вытаскивает из неё
ссылки. Решение, какая из них нужна, принимает адаптер — здесь его нет.

Границы жёсткие. Адрес берётся только из реестра, размер ограничен,
редиректы разрешены, тело обрезается. Ходить по произвольной ссылке этот
код не умеет: иначе диагностика превратилась бы в способ ходить в сеть
чужими руками.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .reach import USER_AGENT

#: Каталог — это страница со ссылками, а не выгрузка. Двухсот килобайт
#: хватает на список; всё, что больше, — уже данные, и тянуть их сюда
#: незачем.
MAX_BYTES = 200_000

#: Ссылок в ответе. Каталоги ведомств содержат сотни пунктов навигации, и
#: полный список в логе деплоя нечитаем.
MAX_LINKS = 40

#: Что похоже на выгрузку данных, а не на пункт меню.
DATA_SUFFIXES = (".zip", ".csv", ".xml", ".json", ".xlsx", ".rar")


@dataclass
class Catalogue:
    """Что нашлось по одной точке входа."""

    url: str
    status: int | None = None
    content_type: str = ""
    size: int = 0
    #: Ссылки, похожие на данные или на карточку набора.
    matched: list[str] = field(default_factory=list)
    #: Первые ссылки как есть — на случай, если фильтр промахнулся.
    sample: list[str] = field(default_factory=list)
    #: Начало текста: у машинных ответов там и лежит ответ.
    head: str = ""
    error: str = ""


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ("a", "link"):
            return
        for name, value in attrs:
            if name == "href" and value:
                self.found.append(value)


def fetch(url: str, *, timeout: float = 20.0, opener=None) -> Catalogue:
    """Забрать точку входа и вытащить из неё ссылки."""
    result = Catalogue(url=url)
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json,*/*",
        },
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            result.status = response.status
            result.content_type = response.headers.get("Content-Type", "")
            body = response.read(MAX_BYTES)
    except urllib.error.HTTPError as error:
        result.status = error.code
        result.error = f"источник ответил {error.code}"
        return result
    except Exception as error:  # noqa: BLE001 — причина важнее типа
        result.error = f"{type(error).__name__}: {error}"
        return result

    result.size = len(body)
    text = body.decode("utf-8", errors="replace")
    result.head = _squash(text)[:600]
    _collect(result, text, url)
    return result


def _collect(result: Catalogue, text: str, base: str) -> None:
    """Ссылки из HTML, а при неудаче — из текста.

    Машинные ответы каталогов бывают и JSON-ом, и XML-ом; разбирать их
    HTML-парсером бессмысленно, но адреса внутри всё равно видны.
    """
    parser = _Links()
    try:
        parser.feed(text)
    except Exception:  # noqa: BLE001 — битую разметку не чиним
        pass

    raw = parser.found or re.findall(r'https?://[^\s"\'<>\\]+', text)
    absolute = [urljoin(base, href) for href in raw]
    seen: set[str] = set()
    unique = [u for u in absolute if not (u in seen or seen.add(u))]

    host = urlsplit(base).hostname or ""
    result.sample = unique[:15]
    result.matched = [
        url
        for url in unique
        if _looks_like_data(url, host)
    ][:MAX_LINKS]


def _looks_like_data(url: str, host: str) -> bool:
    """Похоже ли на выгрузку или карточку набора, а не на пункт меню."""
    lowered = url.lower()
    if lowered.endswith(DATA_SUFFIXES):
        return True
    if urlsplit(url).hostname not in (host, "file.nalog.ru", "data.nalog.ru"):
        return False
    return "opendata" in lowered or "dataservice" in lowered


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


__all__ = ["Catalogue", "MAX_BYTES", "MAX_LINKS", "fetch"]
