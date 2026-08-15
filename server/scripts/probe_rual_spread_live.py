from __future__ import annotations

from collections import defaultdict
from urllib.request import Request, urlopen

from app.research.adapters import rosstat_prices
from app.research.reach import USER_AGENT

TIMEOUT = 45
SERIES = {
    ("24.42.11", "168"): "unwrought_aluminium",
    ("24.42.12", "168"): "alumina",
}
MIN_MONTHS = 18
MIN_COMMON_COMPLETE_QUARTERS = 6


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
    kwargs = {"timeout": TIMEOUT}
    context = rosstat_prices.tls_context_for(url)
    if context is not None:
        kwargs["context"] = context
    with urlopen(request, **kwargs) as response:
        body = response.read()
        print(f"HTTP {response.status} {response.geturl()} bytes={len(body)}")
        return body


def quarter_key(month) -> tuple[int, int]:
    return month.year, (month.month - 1) // 3 + 1


def complete_quarters(months: set) -> set[tuple[int, int]]:
    grouped: dict[tuple[int, int], set[int]] = defaultdict(set)
    for month in months:
        grouped[quarter_key(month)].add(month.month)
    result: set[tuple[int, int]] = set()
    for (year, quarter), seen_months in grouped.items():
        expected = set(range((quarter - 1) * 3 + 1, (quarter - 1) * 3 + 4))
        if seen_months == expected:
            result.add((year, quarter))
    return result


def main() -> None:
    html = fetch(rosstat_prices.CATALOG_URL).decode("utf-8", errors="replace")
    workbook_url = rosstat_prices.discover_workbook(html)
    print(f"WORKBOOK {workbook_url}")
    raw = fetch(workbook_url)
    points = rosstat_prices.parse_workbook(raw)
    print(f"POINTS {len(points)}")

    months_by_series: dict[tuple[str, str], set] = {key: set() for key in SERIES}
    for point in points:
        key = point.product.key
        if key in months_by_series:
            months_by_series[key].add(point.period)

    quarter_sets: list[set[tuple[int, int]]] = []
    for key, label in SERIES.items():
        months = months_by_series[key]
        assert len(months) >= MIN_MONTHS, f"{label}: only {len(months)} monthly values"
        ordered = sorted(months)
        quarters = complete_quarters(months)
        quarter_sets.append(quarters)
        print(
            f"SERIES {label} okpd2={key[0]} okei={key[1]} "
            f"months={len(months)} first={ordered[0].isoformat()} "
            f"last={ordered[-1].isoformat()} complete_quarters={len(quarters)}"
        )

    common = set.intersection(*quarter_sets)
    ordered_common = sorted(common)
    assert len(common) >= MIN_COMMON_COMPLETE_QUARTERS, (
        f"only {len(common)} common complete quarters: {ordered_common}"
    )
    print(
        "COMMON_COMPLETE_QUARTERS "
        + ",".join(f"{year}-Q{quarter}" for year, quarter in ordered_common)
    )
    print("RUAL_SPREAD_LIVE_ACCEPTANCE PASS")


if __name__ == "__main__":
    main()
