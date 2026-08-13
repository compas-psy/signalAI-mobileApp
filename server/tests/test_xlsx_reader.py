from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.research import xlsx_reader


def workbook() -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Item</t></si><si><t>Item A</t></si></sst>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>Unit</t></is></c><c r="C1" t="inlineStr"><is><t>Period</t></is></c></row><row r="2"><c r="A2" t="s"><v>1</v></c><c r="C2"><v>123.45</v></c></row></sheetData></worksheet>',
        )
    return buffer.getvalue()


def test_reads_sheet_cells_and_preserves_missing_columns():
    assert xlsx_reader.read_xlsx(workbook()) == {
        "Data": [["Item", "Unit", "Period"], ["Item A", "", "123.45"]]
    }


def test_invalid_xlsx_fails_closed():
    with pytest.raises(xlsx_reader.WorkbookFormatError):
        xlsx_reader.read_xlsx(b"not-a-workbook")
