from datetime import UTC, datetime

from app.market.http import FetchReport
from app.market.review_resilience import CORE_FUTURES_ROOTS, sync_futures_core_seeded

NOW = datetime(2026, 8, 17, 9, tzinfo=UTC)


def _report(url: str) -> FetchReport:
    return FetchReport(url=url, status=200, elapsed_ms=1, bytes_read=1, ok=True)


def _quiet_board(url: str):
    payload = {
        "securities": {
            "columns": [
                "SECID", "SHORTNAME", "LASTTRADEDATE", "MINSTEP", "STEPPRICE", "DECIMALS"
            ],
            "data": [
                ["CRU6", "CNY-9.26", "2026-09-17", "0.001", "1", 3],
                ["CRZ6", "CNY-12.26", "2026-12-17", "0.001", "1", 3],
                ["ZZU6", "ZZ-9.26", "2026-09-17", "1", "1", 0],
                ["ZZZ6", "ZZ-12.26", "2026-12-17", "1", "1", 0],
            ],
        },
        "marketdata": {
            "columns": [
                "SECID", "LAST", "VALTODAY", "OPENPOSITION", "UPDATETIME", "BID", "OFFER"
            ],
            "data": [
                # Both roots are below the 10m discovery floor at this snapshot.
                ["CRU6", "11.10", "1000000", "500000", "12:00", "11.099", "11.101"],
                ["CRZ6", "11.20", "0", "100000", "12:00", "11.199", "11.201"],
                ["ZZU6", "100", "1000000", "1000", "12:00", "99", "101"],
                ["ZZZ6", "101", "0", "1000", "12:00", "100", "102"],
            ],
        },
    }
    return payload, _report(url)


def test_owner_core_roots_cover_liquid_macro_forts():
    assert {"SI", "CR", "GD", "GL", "SV", "S2", "BR", "NG"} <= CORE_FUTURES_ROOTS


def test_quiet_core_root_is_seeded_for_history_ingest_but_not_auto_admitted(session):
    kept = sync_futures_core_seeded(session, now=NOW, fetch=_quiet_board)

    by_symbol = {item.symbol: item for item in kept}
    assert "CRU6" in by_symbol
    assert by_symbol["CRU6"].is_tradable is False
    assert "core_discovery_seed" not in (by_symbol["CRU6"].metadata_json or {})

    # A non-core root with the exact same weak current turnover remains bounded
    # out. The change is observability for the core set, not a global relaxation.
    assert "ZZU6" not in by_symbol
