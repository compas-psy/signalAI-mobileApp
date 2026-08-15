from __future__ import annotations

from io import BytesIO
from urllib.request import Request, urlopen
from zipfile import ZipFile

from app.research.adapters import rosstat_live_prices, rosstat_prices
from app.research.reach import USER_AGENT

TIMEOUT = 45
PREFIXES = ("05.10", "07.10", "19.10", "24.10", "24.20", "24.31", "24.32")
KEYWORDS = ("кокс", "уголь кокс", "руда желез", "окатыш", "чугун", "сталь", "прокат")


def fetch(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet,*/*"
            ),
        },
    )
    context = rosstat_prices.tls_context_for(url)
    kwargs = {"timeout": TIMEOUT}
    if context is not None:
        kwargs["context"] = context
    with urlopen(request, **kwargs) as response:
        body = response.read()
        print(f"HTTP {response.status} {response.geturl()} bytes={len(body)}")
        return body


def main() -> None:
    catalogue = fetch(rosstat_prices.CATALOG_URL).decode("utf-8", errors="replace")
    workbook_url = rosstat_prices.discover_workbook(catalogue)
    print(f"WORKBOOK {workbook_url}")
    raw = fetch(workbook_url)

    seen: set[tuple[str, str, str]] = set()
    with ZipFile(BytesIO(raw)) as archive:
        shared = rosstat_prices._shared_strings(archive)
        for sheet_name, path in rosstat_prices._sheet_paths(archive):
            rows = rosstat_prices._sheet_rows(archive, path, shared)
            header = rosstat_live_prices._header(rows)
            if header is None:
                continue
            for row in rows[header.row_index + 1 :]:
                okpd2 = row[header.okpd2_col].strip() if header.okpd2_col < len(row) else ""
                name = row[header.name_col].strip() if header.name_col < len(row) else ""
                lower = name.lower()
                if not okpd2.startswith(PREFIXES) and not any(keyword in lower for keyword in KEYWORDS):
                    continue
                unit = (
                    row[header.unit_col].strip()
                    if header.unit_col is not None and header.unit_col < len(row)
                    else ""
                )
                key = (okpd2, name, unit)
                if key in seen:
                    continue
                seen.add(key)
                print(
                    f"CANDIDATE sheet={sheet_name!r} okpd2={okpd2!r} "
                    f"unit={unit!r} name={name!r}"
                )

    print(f"CANDIDATE_COUNT {len(seen)}")


if __name__ == "__main__":
    main()
