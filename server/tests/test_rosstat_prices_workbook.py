from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.research.adapters import rosstat_prices


def _xlsx(rows: list[list[object]], *, sheet_name: str = "Средние цены") -> bytes:
    """Build a tiny real OOXML workbook without adding an xlsx dependency."""
    shared: list[str] = []
    shared_index: dict[str, int] = {}

    def cell(ref: str, value: object) -> str:
        if isinstance(value, str):
            idx = shared_index.get(value)
            if idx is None:
                idx = len(shared)
                shared_index[value] = idx
                shared.append(value)
            return f'<c r="{ref}" t="s"><v>{idx}</v></c>'
        return f'<c r="{ref}"><v>{value}</v></c>'

    def col_name(index: int) -> str:
        out = ""
        n = index + 1
        while n:
            n, rem = divmod(n - 1, 26)
            out = chr(65 + rem) + out
        return out

    sheet_rows = []
    for row_no, values in enumerate(rows, start=1):
        cells = "".join(
            cell(f"{col_name(col)}{row_no}", value)
            for col, value in enumerate(values)
            if value is not None
        )
        sheet_rows.append(f'<row r="{row_no}">{cells}</row>')

    shared_xml = "".join(f"<si><t>{value}</t></si>" for value in shared)
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets>
            </workbook>''',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
                Target="worksheets/sheet1.xml"/>
              <Relationship Id="rId2"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings"
                Target="sharedStrings.xml"/>
            </Relationships>''',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>{''.join(sheet_rows)}</sheetData>
            </worksheet>''',
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
              count="{len(shared)}" uniqueCount="{len(shared)}">{shared_xml}</sst>''',
        )
    return buffer.getvalue()


def _producer_rows() -> list[list[object]]:
    return [
        ["Средние цены производителей промышленных товаров (услуг)"],
        [
            "Код товара (услуги) по ОКПД2",
            "Код единиц измерения по ОКЕИ",
            "Наименование товара (услуги)",
            "Январь 2026",
            "Февраль 2026",
            "Март 2026",
        ],
        ["19.20.21.100", "168", "Бензин автомобильный", 31481, 32950, 34925],
        ["06.10.10.110", "168", "Нефть сырая", 17290, "…", 23163],
    ]


def test_parse_workbook_reads_identity_and_monthly_decimals():
    points = rosstat_prices.parse_workbook(_xlsx(_producer_rows()))

    gasoline = [p for p in points if p.product.okpd2 == "19.20.21.100"]
    assert [p.period.isoformat() for p in gasoline] == [
        "2026-01-01",
        "2026-02-01",
        "2026-03-01",
    ]
    assert [p.value for p in gasoline] == [
        Decimal("31481"),
        Decimal("32950"),
        Decimal("34925"),
    ]
    assert gasoline[0].product.key == ("19.20.21.100", "168")


def test_parse_workbook_drops_missing_values_instead_of_inventing_zero():
    points = rosstat_prices.parse_workbook(_xlsx(_producer_rows()))
    oil = [p for p in points if p.product.okpd2 == "06.10.10.110"]

    assert [p.period.month for p in oil] == [1, 3]
    assert [p.value for p in oil] == [Decimal("17290"), Decimal("23163")]


def test_parse_workbook_accepts_decimal_comma_and_non_breaking_spaces():
    rows = _producer_rows()
    rows[2][3] = "31\u00a0481,50"
    points = rosstat_prices.parse_workbook(_xlsx(rows))
    january = next(
        p
        for p in points
        if p.product.okpd2 == "19.20.21.100" and p.period.month == 1
    )
    assert january.value == Decimal("31481.50")


def test_parse_workbook_fails_closed_when_classifier_headers_change():
    rows = _producer_rows()
    rows[1][0] = "Код продукта"
    with pytest.raises(rosstat_prices.WorkbookSchemaError, match="ОКПД2"):
        rosstat_prices.parse_workbook(_xlsx(rows))


def test_parse_workbook_rejects_duplicate_identity_period():
    rows = _producer_rows()
    rows.append(["19.20.21.100", "168", "Новое название бензина", 99999, None, None])
    with pytest.raises(rosstat_prices.DuplicatePricePoint):
        rosstat_prices.parse_workbook(_xlsx(rows))


def test_product_label_revision_does_not_change_series_identity():
    first = rosstat_prices.ProductIdentity.create(
        okpd2="19.20.21.100", okei="168", name="Бензин автомобильный"
    )
    renamed = rosstat_prices.ProductIdentity.create(
        okpd2="19.20.21.100", okei="168", name="Бензины автомобильные"
    )
    assert first.key == renamed.key
    assert rosstat_prices.observation_type(first) == rosstat_prices.observation_type(renamed)
