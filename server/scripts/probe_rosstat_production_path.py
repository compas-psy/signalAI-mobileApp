from __future__ import annotations

from collections import defaultdict
from urllib.request import Request, urlopen

from app.research.adapters import rosstat_prices
from app.research.reach import USER_AGENT

TIMEOUT = 45
TARGETS = {
    ("19.20.21.100", "168"),
    ("06.10.10.200", "168"),
}


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

    points = rosstat_prices.parse_workbook(fetch(workbook_url))
    print(f"POINTS {len(points)}")

    by_product = defaultdict(list)
    for point in points:
        by_product[point.product.key].append(point)

    found = set()
    for key, rows in sorted(by_product.items()):
        if key not in TARGETS:
            continue
        found.add(key)
        product = rows[0].product
        earliest = min(row.period for row in rows)
        latest = max(row.period for row in rows)
        latest_value = next(row.value for row in rows if row.period == latest)
        years = sorted({row.period.year for row in rows})
        print(
            "TARGET_SERIES "
            f"okpd2={product.okpd2} okei={product.okei} "
            f"name={product.name!r} earliest={earliest.isoformat()} "
            f"latest={latest.isoformat()} latest_value={latest_value} "
            f"count={len(rows)} years={years} "
            f"observation_type={rosstat_prices.observation_type(product)}"
        )

    missing = sorted(TARGETS - found)
    if missing:
        raise SystemExit(f"TARGETS_MISSING {missing}")
    print("TARGETS_OK")


if __name__ == "__main__":
    main()
