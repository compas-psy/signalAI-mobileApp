"""Parser for the live Rosstat monthly producer-price layouts (2021+).

The current workbook no longer carries numeric OKEI in its monthly sheets.
Identity therefore remains OKPD2 + OKEI only where the unit can be mapped by
an explicit classifier alias. Cross-sheet unit evidence must agree before a
unit-less regional sheet (2024-2025) is accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .rosstat_prices import (
    InvalidProductIdentity,
    ProducerPricePoint,
    ProductIdentity,
    WorkbookSchemaError,
    _MONTH_PREFIXES,
    _OKPD2,
    _YEAR,
    _decimal,
    _normalize,
)

# Deliberately narrow classifier bridge. These are OKEI codes, not guessed
# display units: current OKEI assigns 166 to kilogram and 168 to tonne.
# New aliases/codes must be added explicitly with regression coverage before
# the live parser can emit them.
_UNIT_TO_OKEI = {
    "т": "168",
    "тонн": "168",
    "тонна": "168",
    "тонны": "168",
    "кг": "166",
    "килограмм": "166",
    "килограмма": "166",
    "килограммов": "166",
}


@dataclass(frozen=True, slots=True)
class _LiveHeader:
    row_index: int
    okpd2_col: int
    name_col: int
    unit_col: int | None
    periods: dict[int, date]


def _at(row: list[str], index: int) -> str:
    return row[index] if index < len(row) else ""


def _month_number(value: str) -> int | None:
    normalized = _normalize(value)
    return next(
        (
            number
            for prefix, number in _MONTH_PREFIXES.items()
            if re.search(rf"\b{prefix}\w*\b", normalized)
        ),
        None,
    )


def _sheet_year(rows: list[list[str]], header_index: int) -> int:
    years = {
        int(match.group(1))
        for row in rows[:header_index]
        for value in row
        for match in _YEAR.finditer(_normalize(value))
    }
    if len(years) != 1:
        raise WorkbookSchemaError(
            "live Rosstat monthly sheet has missing or ambiguous year"
        )
    return years.pop()


def _header(rows: list[list[str]]) -> _LiveHeader | None:
    for row_index, row in enumerate(rows):
        normalized = [_normalize(value) for value in row]
        okpd2 = [index for index, value in enumerate(normalized) if "окпд2" in value]
        okei = [index for index, value in enumerate(normalized) if "океи" in value]
        if okei:
            # The legacy explicit-OKEI layout is handled by rosstat_prices.
            continue
        months = {
            index: month
            for index, value in enumerate(row)
            if (month := _month_number(value)) is not None
        }
        if not okpd2 or not months:
            continue
        if len(okpd2) != 1:
            raise WorkbookSchemaError("ambiguous live Rosstat OKPD2 column")
        if len(set(months.values())) != len(months):
            raise WorkbookSchemaError("duplicate month columns in live Rosstat sheet")
        unit_columns = [
            index
            for index, value in enumerate(normalized)
            if "единиц" in value and "измерен" in value
        ]
        if len(unit_columns) > 1:
            raise WorkbookSchemaError("ambiguous live Rosstat unit column")
        name_col = okpd2[0] - 1
        if name_col < 0:
            raise WorkbookSchemaError("live Rosstat sheet has no product-name column")
        year = _sheet_year(rows, row_index)
        return _LiveHeader(
            row_index=row_index,
            okpd2_col=okpd2[0],
            name_col=name_col,
            unit_col=unit_columns[0] if unit_columns else None,
            periods={column: date(year, month, 1) for column, month in months.items()},
        )
    return None


def _okei_for_label(label: str) -> str | None:
    normalized = _normalize(label).strip(" .,;")
    return _UNIT_TO_OKEI.get(normalized)


def _name_and_embedded_unit(name: str) -> tuple[str, str | None]:
    cleaned = " ".join(name.split())
    if "," not in cleaned:
        return cleaned, None
    candidate_name, candidate_unit = cleaned.rsplit(",", 1)
    okei = _okei_for_label(candidate_unit)
    if okei is None:
        return cleaned, None
    return candidate_name.strip(), okei


def _unit_evidence(
    row: list[str], header: _LiveHeader
) -> tuple[str, str, str] | None:
    okpd2 = _at(row, header.okpd2_col).strip()
    if not okpd2:
        return None
    if not _OKPD2.fullmatch(okpd2):
        raise WorkbookSchemaError(f"invalid live Rosstat OKPD2: {okpd2!r}")
    raw_name = _at(row, header.name_col).strip()
    name, embedded_okei = _name_and_embedded_unit(raw_name)
    explicit_okei = (
        _okei_for_label(_at(row, header.unit_col))
        if header.unit_col is not None
        else None
    )
    if explicit_okei is not None and embedded_okei is not None:
        if explicit_okei != embedded_okei:
            raise WorkbookSchemaError(f"conflicting unit evidence for OKPD2 {okpd2}")
    okei = explicit_okei or embedded_okei
    if okei is None:
        return None
    if not name:
        raise WorkbookSchemaError(f"empty live Rosstat product name for {okpd2}")
    return okpd2, okei, name


def _collect_units(
    sheets: list[tuple[list[list[str]], _LiveHeader]],
) -> tuple[dict[str, str], dict[str, str]]:
    units: dict[str, str] = {}
    names: dict[str, str] = {}
    for rows, header in sheets:
        for row in rows[header.row_index + 1 :]:
            evidence = _unit_evidence(row, header)
            if evidence is None:
                continue
            okpd2, okei, name = evidence
            previous = units.get(okpd2)
            if previous is not None and previous != okei:
                raise WorkbookSchemaError(f"conflicting unit evidence for OKPD2 {okpd2}")
            units[okpd2] = okei
            names.setdefault(okpd2, name)
    return units, names


def _point_values(
    row: list[str], product: ProductIdentity, header: _LiveHeader
) -> list[ProducerPricePoint]:
    result: list[ProducerPricePoint] = []
    for column, period in header.periods.items():
        value = _decimal(_at(row, column))
        if value is not None:
            result.append(ProducerPricePoint(product=product, period=period, value=value))
    return result


def _parse_sheet(
    rows: list[list[str]],
    header: _LiveHeader,
    units: dict[str, str],
    names: dict[str, str],
) -> list[ProducerPricePoint]:
    result: list[ProducerPricePoint] = []
    pending_regional_product: ProductIdentity | None = None

    for row in rows[header.row_index + 1 :]:
        okpd2 = _at(row, header.okpd2_col).strip()
        raw_name = _at(row, header.name_col).strip()

        if okpd2:
            if not _OKPD2.fullmatch(okpd2):
                raise WorkbookSchemaError(f"invalid live Rosstat OKPD2: {okpd2!r}")
            okei = units.get(okpd2)
            if okei is None:
                pending_regional_product = None
                continue
            name, _embedded = _name_and_embedded_unit(raw_name)
            if not name:
                name = names.get(okpd2, "")
            try:
                product = ProductIdentity.create(okpd2=okpd2, okei=okei, name=name)
            except InvalidProductIdentity as error:
                raise WorkbookSchemaError(
                    f"invalid live OKPD2/OKEI product identity: {okpd2!r}/{okei!r}"
                ) from error

            direct_points = _point_values(row, product, header)
            if direct_points:
                result.extend(direct_points)
                pending_regional_product = None
            else:
                pending_regional_product = product
            continue

        if (
            pending_regional_product is not None
            and _normalize(raw_name) == "российская федерация"
        ):
            result.extend(_point_values(row, pending_regional_product, header))
            # Federal-district rows after the national row must never be folded
            # into the national producer-price series.
            pending_regional_product = None

    return result


def parse_sheets(rows_by_sheet: list[list[list[str]]]) -> tuple[list[ProducerPricePoint], int]:
    """Parse current monthly layouts and return points + matched sheet count."""
    matched: list[tuple[list[list[str]], _LiveHeader]] = []
    for rows in rows_by_sheet:
        header = _header(rows)
        if header is not None:
            matched.append((rows, header))
    if not matched:
        return [], 0

    units, names = _collect_units(matched)
    points: list[ProducerPricePoint] = []
    for rows, header in matched:
        points.extend(_parse_sheet(rows, header, units, names))
    return points, len(matched)


__all__ = ["parse_sheets"]
