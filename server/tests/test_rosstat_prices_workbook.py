from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.research.adapters import rosstat_prices


def _xlsx_sheets(sheets: list[tuple[str, list[list[object]]]]) -> bytes:
    """Build a tiny real multi-sheet OOXML workbook without an xlsx dependency."""
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

    sheet_xml: list[tuple[str, str]] = []
    for sheet_index, (sheet_name, rows) in enumerate(sheets, start=1):
        sheet_rows = []
        for row_no, values in enumerate(rows, start=1):
            cells = "".join(
                cell(f"{col_name(col)}{row_no}", value)
                for col, value in enumerate(values)
                if value is not None
            )
            sheet_rows.append(f'<row r="{row_no}">{cells}</row>')
        sheet_xml.append(
            (
                sheet_name,
                f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
                <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                  <sheetData>{''.join(sheet_rows)}</sheetData>
                </worksheet>''',
            )
        )

    shared_xml = "".join(f"<si><t>{value}</t></si>" for value in shared)
    workbook_sheets = "".join(
        f'<sheet name="{name}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _xml) in enumerate(sheet_xml, start=1)
    )
    relationships = "".join(
        f'''<Relationship Id="rId{index}"
          Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
          Target="worksheets/sheet{index}.xml"/>'''
        for index in range(1, len(sheet_xml) + 1)
    )
    shared_relation = len(sheet_xml) + 1

    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets>{workbook_sheets}</sheets>
            </workbook>''',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              {relationships}
              <Relationship Id="rId{shared_relation}"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings"
                Target="sharedStrings.xml"/>
            </Relationships>''',
        )
        for index, (_name, xml) in enumerate(sheet_xml, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", xml)
        archive.writestr(
            "xl/sharedStrings.xml",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
              count="{len(shared)}" uniqueCount="{len(shared)}">{shared_xml}</sst>''',
        )
    return buffer.getvalue()


def _xlsx(rows: list[list[object]], *, sheet_name: str = "Средние цены") -> bytes:
    return _xlsx_sheets([(sheet_name, rows)])


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


def _live_direct_rows(year: int) -> list[list[object]]:
    return [
        [
            "Средние цены производителей промышленных товаров (услуг)\n"
            f"по Российской Федерации в {year} г."
        ],
        ["на конец периода, рублей за единицу измерения"],
        ["", "Код товара на основе ОКПД2", "январь", "февраль", "март"],
        [
            "Нефть обезвоженная, обессоленная и стабилизированная, т",
            "06.10.10.200",
            20208.93,
            21273.35,
            23235.95,
        ],
        ["Бензин автомобильный, т", "19.20.21.100", 16432.31, 17059.11, 23665.83],
        ["Газ горючий природный, тыс. м3", "06.20.10.110", 3054.05, 3076.46, 3078.78],
    ]


def _live_regional_rows(
    year: int,
    *,
    with_units: bool,
    gasoline_unit: str = "тонн",
    oil_unit: str = "тонн",
) -> list[list[object]]:
    if with_units:
        header = [
            "",
            "Код товара на основе ОКПД2",
            "Единицы измерения",
            "январь",
            "февраль",
            "март",
        ]
        oil_header = [
            "Нефть обезвоженная, обессоленная и стабилизированная",
            "06.10.10.200",
            oil_unit,
            "",
            "",
            "",
        ]
        oil_ru = ["Российская Федерация", "", "", 20108.44, 20660.47, 23163.48]
        gasoline_header = [
            "Бензин автомобильный",
            "19.20.21.100",
            gasoline_unit,
            "",
            "",
            "",
        ]
        gasoline_ru = ["Российская Федерация", "", "", 30205.87, 31480.85, 34924.56]
        distractor = ["Центральный федеральный округ", "", "", 99999, 99999, 99999]
    else:
        header = ["", "Код товара на основе ОКПД2", "январь", "февраль", "март"]
        oil_header = [
            "Нефть обезвоженная, обессоленная и стабилизированная",
            "06.10.10.200",
            "",
            "",
            "",
        ]
        oil_ru = ["Российская Федерация", "", 38180.94, 39587.26, 42859.12]
        gasoline_header = ["Бензин автомобильный", "19.20.21.100", "", "", ""]
        gasoline_ru = ["Российская Федерация", "", 23032.27, 25807.75, 29821.81]
        distractor = ["Центральный федеральный округ", "", 99999, 99999, 99999]
    return [
        [
            "Средние цены производителей промышленных товаров (услуг)\n"
            f"по Российской Федерации и федеральным округам в {year} г."
        ],
        ["на конец периода, рублей за единицу измерения"],
        header,
        oil_header,
        oil_ru,
        distractor,
        gasoline_header,
        gasoline_ru,
        distractor,
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


def test_live_direct_layout_uses_sheet_year_and_tonne_unit_alias():
    points = rosstat_prices.parse_workbook(_xlsx(_live_direct_rows(2023), sheet_name="6"))

    oil = [p for p in points if p.product.okpd2 == "06.10.10.200"]
    gasoline = [p for p in points if p.product.okpd2 == "19.20.21.100"]
    assert [p.period.isoformat() for p in oil] == [
        "2023-01-01",
        "2023-02-01",
        "2023-03-01",
    ]
    assert oil[0].product.key == ("06.10.10.200", "168")
    assert gasoline[0].product.key == ("19.20.21.100", "168")
    assert not [p for p in points if p.product.okpd2 == "06.20.10.110"]


def test_live_2026_regional_layout_reads_only_russian_federation_row():
    points = rosstat_prices.parse_workbook(
        _xlsx(_live_regional_rows(2026, with_units=True), sheet_name="9")
    )

    gasoline = [p for p in points if p.product.okpd2 == "19.20.21.100"]
    assert [p.value for p in gasoline] == [
        Decimal("30205.87"),
        Decimal("31480.85"),
        Decimal("34924.56"),
    ]
    assert gasoline[0].product.key == ("19.20.21.100", "168")
    assert Decimal("99999") not in [p.value for p in points]


def test_live_2024_layout_reuses_consistent_unit_evidence_from_2026_sheet():
    content = _xlsx_sheets(
        [
            ("7", _live_regional_rows(2024, with_units=False)),
            ("9", _live_regional_rows(2026, with_units=True)),
        ]
    )
    points = rosstat_prices.parse_workbook(content)

    gasoline_2024 = [
        p
        for p in points
        if p.product.okpd2 == "19.20.21.100" and p.period.year == 2024
    ]
    oil_2024 = [
        p
        for p in points
        if p.product.okpd2 == "06.10.10.200" and p.period.year == 2024
    ]
    assert len(gasoline_2024) == 3
    assert len(oil_2024) == 3
    assert gasoline_2024[0].product.okei == "168"
    assert oil_2024[0].product.okei == "168"


def test_live_layout_conflicting_unit_evidence_for_same_okpd2_fails_closed():
    content = _xlsx_sheets(
        [
            ("2023", _live_direct_rows(2023)),
            (
                "2026",
                _live_regional_rows(
                    2026,
                    with_units=True,
                    gasoline_unit="кг",
                ),
            ),
        ]
    )
    with pytest.raises(rosstat_prices.WorkbookSchemaError, match="conflicting unit"):
        rosstat_prices.parse_workbook(content)


def test_live_layout_unsupported_unit_is_not_misclassified_as_tonnes():
    rows = _live_direct_rows(2023)
    rows[5][0] = "Газ горючий природный, тыс. м3"
    points = rosstat_prices.parse_workbook(_xlsx(rows, sheet_name="6"))

    assert not [p for p in points if p.product.okpd2 == "06.20.10.110"]
