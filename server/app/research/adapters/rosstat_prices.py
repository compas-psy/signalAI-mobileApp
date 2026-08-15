"""Rosstat producer-price catalogue discovery and workbook parsing for SPREAD.

Rosstat replaces the downloadable XLSX as new monthly vintages are published,
so the adapter discovers the current workbook from the official price catalogue
instead of pinning a dated media-bank URL. Parsing is deliberately strict:
classifier identity comes from OKPD2 + OKEI, missing values stay missing, and a
changed workbook schema stops collection instead of silently shifting columns.
"""

from __future__ import annotations

import posixpath
import re
import ssl
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zipfile import BadZipFile, ZipFile

from ..codes import observation_code

SOURCE_ID = "rosstat"
CATALOG_URL = "https://rosstat.gov.ru/statistics/price"
DATASET_TITLE = "Средние цены производителей промышленных товаров (услуг) с 1998 г."
_ALLOWED_HOSTS = {"rosstat.gov.ru", "www.rosstat.gov.ru"}
_CURRENT_WORKBOOK = re.compile(
    r"^Proizvoditeli_Cena_\d{2}-\d{4}\.xlsx$", re.IGNORECASE
)
_CA_BUNDLE = Path(__file__).parent / "certs" / "rosstat-russian-trusted-ca.crt"
_OKPD2 = re.compile(r"^\d{2}(?:\.\d{1,3}){1,4}$")
_OKEI = re.compile(r"^\d{3}$")
_CELL_REF = re.compile(r"^([A-Z]+)\d+$")
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_M = "{" + _MAIN_NS + "}"
_R = "{" + _REL_NS + "}"
_P = "{" + _PKG_REL_NS + "}"

_MONTH_PREFIXES = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}
_MISSING = {"", "-", "…", "...", "н/д", "н.д."}


class DatasetNotFound(LookupError):
    """The official catalogue has no usable link for the required dataset."""


class AmbiguousDataset(LookupError):
    """The catalogue contains more than one exact usable dataset link."""


class InvalidProductIdentity(ValueError):
    """The row has no usable OKPD2/OKEI identity."""


class WorkbookSchemaError(ValueError):
    """The XLSX cannot be mapped to the expected Rosstat producer-price schema."""


class DuplicatePricePoint(ValueError):
    """One OKPD2/OKEI + month appears more than once in the workbook."""


@dataclass(frozen=True, slots=True)
class ProductIdentity:
    okpd2: str
    okei: str
    name: str

    @classmethod
    def create(cls, *, okpd2: str, okei: str, name: str) -> "ProductIdentity":
        normalized_okpd2 = okpd2.strip()
        normalized_okei = okei.strip()
        normalized_name = " ".join(name.split())
        if not _OKPD2.fullmatch(normalized_okpd2):
            raise InvalidProductIdentity("invalid OKPD2")
        if not _OKEI.fullmatch(normalized_okei):
            raise InvalidProductIdentity("invalid OKEI")
        if not normalized_name:
            raise InvalidProductIdentity("empty product name")
        return cls(
            okpd2=normalized_okpd2,
            okei=normalized_okei,
            name=normalized_name,
        )

    @property
    def key(self) -> tuple[str, str]:
        return (self.okpd2, self.okei)


@dataclass(frozen=True, slots=True)
class ProducerPricePoint:
    product: ProductIdentity
    period: date
    value: Decimal


def observation_type(product: ProductIdentity) -> str:
    """Stable machine key for one producer-price series.

    Human-readable labels may be revised between source vintages. Classifier
    identifiers are the identity, so a label change must not create a second
    logical observation series.
    """
    return observation_code(
        SOURCE_ID,
        "producer_price",
        product.okpd2.replace(".", "_"),
        product.okei,
    )


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._href: str | None = None
        self._text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self._href = next(
            (value for key, value in attrs if key.lower() == "href"), None
        )
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


def tls_context() -> ssl.SSLContext:
    """Strict trust context for Rosstat's Ministry-issued TLS chain only."""
    return ssl.create_default_context(cafile=str(_CA_BUNDLE))


def tls_context_for(url: str) -> ssl.SSLContext | None:
    """Return the pinned context only for official Rosstat HTTPS hosts."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        return None
    return tls_context()


def discover_workbook(html: str) -> str:
    """Return the one exact current producer-price XLSX from the price page.

    Rosstat's current page renders XLSX anchors with an icon-only label, so the
    live contract is the narrow `Proizvoditeli_Cena_MM-YYYY.xlsx` filename.
    The historical exact title remains accepted for compatibility. Any second
    distinct matching URL fails closed instead of guessing which vintage is data.
    """
    parser = _Links()
    parser.feed(html)

    matches: set[str] = set()
    for href, title in parser.links:
        url = _usable_xlsx(href)
        if url is None:
            continue
        filename = posixpath.basename(urlparse(url).path)
        if title == DATASET_TITLE or _CURRENT_WORKBOOK.fullmatch(filename):
            matches.add(url)

    if not matches:
        raise DatasetNotFound(DATASET_TITLE)
    if len(matches) != 1:
        raise AmbiguousDataset(DATASET_TITLE)
    return matches.pop()


def _normalize(value: str) -> str:
    return " ".join(
        value.replace("\u00a0", " ")
        .replace("\n", " ")
        .lower()
        .replace("ё", "е")
        .split()
    )


def _period(value: str) -> date | None:
    normalized = _normalize(value)
    year_match = _YEAR.search(normalized)
    if year_match is None:
        return None
    month = next(
        (
            number
            for prefix, number in _MONTH_PREFIXES.items()
            if re.search(rf"\b{prefix}\w*\b", normalized)
        ),
        None,
    )
    if month is None:
        return None
    return date(int(year_match.group(1)), month, 1)


def _column_index(reference: str) -> int:
    match = _CELL_REF.fullmatch(reference)
    if match is None:
        raise WorkbookSchemaError(f"invalid XLSX cell reference: {reference!r}")
    result = 0
    for char in match.group(1):
        result = result * 26 + ord(char) - 64
    return result - 1


def _shared_strings(archive: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.iter(_M + "t"))
        for item in root.findall(_M + "si")
    ]


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.attrib.get("t")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(_M + "t"))
    value = cell.find(_M + "v")
    if value is None or value.text is None:
        return ""
    raw = value.text
    if kind != "s":
        return raw
    try:
        return shared[int(raw)]
    except (ValueError, IndexError) as error:
        raise WorkbookSchemaError("invalid XLSX shared-string reference") from error


def _sheet_paths(archive: ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in relationships.findall(_P + "Relationship")
    }
    result: list[tuple[str, str]] = []
    for sheet in workbook.findall(_M + "sheets/" + _M + "sheet"):
        relation_id = sheet.attrib.get(_R + "id", "")
        target = targets.get(relation_id)
        if not target:
            raise WorkbookSchemaError("XLSX sheet has no workbook relationship")
        path = target.lstrip("/")
        if not path.startswith("xl/"):
            path = posixpath.normpath(posixpath.join("xl", path))
        if not path.startswith("xl/") or path.startswith("../"):
            raise WorkbookSchemaError("XLSX sheet path escapes workbook")
        result.append((sheet.attrib.get("name", ""), path))
    return result


def _sheet_rows(
    archive: ZipFile,
    path: str,
    shared: list[str],
) -> list[list[str]]:
    root = ET.fromstring(archive.read(path))
    rows: list[list[str]] = []
    for row in root.findall(".//" + _M + "sheetData/" + _M + "row"):
        cells: dict[int, str] = {}
        for cell in row.findall(_M + "c"):
            reference = cell.attrib.get("r", "")
            cells[_column_index(reference)] = _cell_value(cell, shared)
        if not cells:
            rows.append([])
            continue
        last = max(cells)
        rows.append([cells.get(index, "") for index in range(last + 1)])
    return rows


def _header(
    rows: list[list[str]],
) -> tuple[int, int, int, int, dict[int, date]] | None:
    for row_index, row in enumerate(rows):
        normalized = [_normalize(value) for value in row]
        okpd2 = [index for index, value in enumerate(normalized) if "окпд2" in value]
        okei = [index for index, value in enumerate(normalized) if "океи" in value]
        names = [
            index
            for index, value in enumerate(normalized)
            if "наименование" in value and ("товар" in value or "продук" in value)
        ]
        periods = {
            index: parsed
            for index, value in enumerate(row)
            if (parsed := _period(value)) is not None
        }
        if not (okpd2 and okei and names and periods):
            continue
        if len(okpd2) != 1 or len(okei) != 1 or len(names) != 1:
            raise WorkbookSchemaError("ambiguous Rosstat classifier columns")
        return row_index, okpd2[0], okei[0], names[0], periods
    return None


def _decimal(value: str) -> Decimal | None:
    normalized = _normalize(value)
    if normalized in _MISSING or normalized == "…1)":
        return None

    # The live 2026 workbook appends footnote `2)` immediately after
    # the second decimal place (for example `12471,552)`). Strip only
    # this observed grammar; arbitrary annotations still fail closed.
    footnoted = re.fullmatch(r"([+-]?\d[\d ]*[,.]\d{2})2\)", normalized)
    if footnoted is not None:
        normalized = footnoted.group(1)

    normalized = normalized.replace(" ", "").replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation as error:
        raise WorkbookSchemaError(f"invalid producer-price value: {value!r}") from error


def _parse_sheet(rows: list[list[str]]) -> list[ProducerPricePoint] | None:
    header = _header(rows)
    if header is None:
        return None
    header_index, okpd2_col, okei_col, name_col, period_cols = header
    result: list[ProducerPricePoint] = []

    for row in rows[header_index + 1 :]:
        def at(index: int) -> str:
            return row[index] if index < len(row) else ""

        okpd2 = at(okpd2_col).strip()
        okei = at(okei_col).strip()
        name = at(name_col).strip()
        # Section labels in Rosstat workbooks have no classifier codes.
        if not okpd2 and not okei:
            continue
        if not okpd2 or not okei:
            raise WorkbookSchemaError("partial OKPD2/OKEI identity in Rosstat row")
        try:
            product = ProductIdentity.create(okpd2=okpd2, okei=okei, name=name)
        except InvalidProductIdentity as error:
            raise WorkbookSchemaError(
                f"invalid OKPD2/OKEI product identity: {okpd2!r}/{okei!r}"
            ) from error

        for column, period in period_cols.items():
            value = _decimal(at(column))
            if value is not None:
                result.append(
                    ProducerPricePoint(product=product, period=period, value=value)
                )
    return result


def parse_workbook(content: bytes) -> list[ProducerPricePoint]:
    """Parse supported monthly producer-price layouts from Rosstat XLSX.

    The adapter preserves the historical explicit-OKEI layout and also accepts
    the live 2021+ layouts where the year is in the sheet title, months are
    separate headers, and 2024+ national values live on a child row. The live
    parser emits only products whose text unit can be mapped explicitly back to
    OKEI and whose cross-sheet unit evidence is consistent.
    """
    try:
        with ZipFile(BytesIO(content)) as archive:
            shared = _shared_strings(archive)
            rows_by_sheet = [
                _sheet_rows(archive, path, shared)
                for _name, path in _sheet_paths(archive)
            ]
            points: list[ProducerPricePoint] = []
            matched_sheets = 0
            seen: set[tuple[tuple[str, str], date]] = set()

            def add(parsed: list[ProducerPricePoint]) -> None:
                for point in parsed:
                    key = (point.product.key, point.period)
                    if key in seen:
                        raise DuplicatePricePoint(
                            f"duplicate Rosstat point {point.product.key} {point.period}"
                        )
                    seen.add(key)
                    points.append(point)

            for rows in rows_by_sheet:
                parsed = _parse_sheet(rows)
                if parsed is None:
                    continue
                matched_sheets += 1
                add(parsed)

            # Function-local import avoids a module import cycle: the live parser
            # reuses the validated identity/value types defined above.
            from . import rosstat_live_prices

            live_points, live_matched = rosstat_live_prices.parse_sheets(
                rows_by_sheet
            )
            matched_sheets += live_matched
            add(live_points)
    except (DuplicatePricePoint, WorkbookSchemaError):
        raise
    except (BadZipFile, KeyError, ET.ParseError) as error:
        raise WorkbookSchemaError("invalid Rosstat XLSX container") from error

    if matched_sheets == 0:
        raise WorkbookSchemaError(
            "Rosstat workbook has no supported ОКПД2/OKPD2 producer-price monthly layout"
        )
    return points


__all__ = [
    "AmbiguousDataset",
    "CATALOG_URL",
    "DATASET_TITLE",
    "DatasetNotFound",
    "DuplicatePricePoint",
    "InvalidProductIdentity",
    "ProducerPricePoint",
    "ProductIdentity",
    "SOURCE_ID",
    "WorkbookSchemaError",
    "discover_workbook",
    "observation_type",
    "parse_workbook",
    "tls_context",
    "tls_context_for",
]
