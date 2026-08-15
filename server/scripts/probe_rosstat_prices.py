from __future__ import annotations

from collections import defaultdict
from urllib.request import Request, urlopen

from app.research.adapters import rosstat_prices
from app.research.reach import USER_AGENT

TIMEOUT = 45
PREFIXES = (
    "06.10.10",  # crude oil
    "06.20.10",  # natural gas
    "07.10.10",  # iron ore
    "19.20.21",  # gasoline / diesel
    "20.15",     # fertilizers
    "24.10",     # iron / steel products
)
TARGETS = {
    ("19.20.21.100", "168"),
    ("06.10.10.200", "168"),
}


def fetch(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*",
        },
    )
    with urlopen(request, timeout=TIMEOUT) as response:
        print(f"HTTP {response.status} {response.geturl()}")
        return response.read()


def main() -> None:
    catalogue = fetch(rosstat_prices.CATALOG_URL).decode("utf-8", errors="replace")
    workbook_url = rosstat_prices.discover_workbook(catalogue)
    print(f"WORKBOOK {workbook_url}")

    points = rosstat_prices.parse_workbook(fetch(workbook_url))
    print(f"POINTS {len(points)}")

    by_product = defaultdict(list)
    for point in points:
        by_product[point.product.key].append(point)

    found_targets = set()
    for key, rows in sorted(by_product.items()):
        product = rows[0].product
        if key in TARGETS:
            found_targets.add(key)
        if key in TARGETS or product.okpd2.startswith(PREFIXES):
            latest = max(row.period for row in rows)
            earliest = min(row.period for row in rows)
            latest_value = next(row.value for row in rows if row.period == latest)
            print(
                "SERIES "
                f"okpd2={product.okpd2} okei={product.okei} "
                f"name={product.name!r} earliest={earliest.isoformat()} "
                f"latest={latest.isoformat()} latest_value={latest_value} count={len(rows)} "
                f"observation_type={rosstat_prices.observation_type(product)}"
            )

    missing = sorted(TARGETS - found_targets)
    if missing:
        raise SystemExit(f"TARGETS_MISSING {missing}")
    print("TARGETS_OK")


if __name__ == "__main__":
    main()
