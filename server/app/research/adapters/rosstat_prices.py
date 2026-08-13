"""Rosstat producer-price catalogue discovery for SPREAD.

Rosstat replaces the downloadable XLSX as new monthly vintages are published,
so the adapter discovers the current workbook from the official price catalogue
instead of pinning a dated media-bank URL. Workbook parsing is a separate slice.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

SOURCE_ID = "rosstat"
CATALOG_URL = "https://rosstat.gov.ru/statistics/price"
DATASET_TITLE = "Средние цены производителей промышленных товаров (услуг) с 1998 г."
_ALLOWED_HOSTS = {"rosstat.gov.ru", "www.rosstat.gov.ru"}


class DatasetNotFound(LookupError):
    """The official catalogue has no usable link for the required dataset."""


class AmbiguousDataset(LookupError):
    """The catalogue contains more than one exact usable dataset link."""


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._href: str | None = None
        self._text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self._href = next((value for key, value in attrs if key.lower() == "href"), None)
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        self.links.append((self._href, " ".join("".join(self._text).split())))
        self._href = None
        self._text = []


def _usable_xlsx(href: str) -> str | None:
    url = urljoin(CATALOG_URL, href.strip())
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        return None
    if not parsed.path.lower().endswith(".xlsx"):
        return None
    return url


def discover_workbook(html: str) -> str:
    """Return the one exact current XLSX target from Rosstat's price page.

    Similar producer-price datasets are deliberately ignored. If Rosstat
    changes the title or temporarily publishes duplicate exact links, collection
    stops visibly instead of silently feeding the wrong series into SPREAD.
    """
    parser = _Links()
    parser.feed(html)

    matches = {
        url
        for href, title in parser.links
        if title == DATASET_TITLE
        if (url := _usable_xlsx(href)) is not None
    }
    if not matches:
        raise DatasetNotFound(DATASET_TITLE)
    if len(matches) != 1:
        raise AmbiguousDataset(DATASET_TITLE)
    return matches.pop()


__all__ = [
    "AmbiguousDataset",
    "CATALOG_URL",
    "DATASET_TITLE",
    "DatasetNotFound",
    "SOURCE_ID",
    "discover_workbook",
]
