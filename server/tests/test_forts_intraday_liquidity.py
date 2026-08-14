"""Regression tests for FORTS universe shrinkage during an active session.

VALTODAY is cumulative *inside the current trading day*.  It must not be used
as if it were a completed-day liquidity statistic: early in the session that
silently removes otherwise liquid contracts for the next review interval.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.market import universe
from app.market.http import FetchReport
from app.models import Bar, Instrument
from app.models.enums import AssetClass, Timeframe, Venue

NOW = datetime(2026, 8, 14, 10, tzinfo=UTC)


def _report(url: str) -> FetchReport:
    return FetchReport(url=url, status=200, elapsed_ms=1, bytes_read=1, ok=True)


def _partial_session_board(url: str):
    payload = {
        "securities": {
            "columns": [
                "SECID", "SHORTNAME", "LASTTRADEDATE", "MINSTEP", "STEPPRICE", "DECIMALS"
            ],
            "data": [
                ["SiU6", "Si-9.26", "2026-09-17", "1", "1", 0],
                ["SiZ6", "Si-12.26", "2026-12-17", "1", "1", 0],
                ["BRU6", "BR-9.26", "2026-09-01", "0.01", "7.5", 2],
                ["BRV6", "BR-10.26", "2026-10-01", "0.01", "7.5", 2],
            ],
        },
        "marketdata": {
            "columns": [
                "SECID", "LAST", "VALTODAY", "OPENPOSITION", "UPDATETIME", "BID", "OFFER"
            ],
            "data": [
                # Si is liquid on completed days, but has traded only 5m RUB so far today.
                ["SiU6", "80000", "5000000", "500000", "13:00", "79999", "80001"],
                ["SiZ6", "81000", "0", "100000", "13:00", "80999", "81001"],
                ["BRU6", "70", "500000000", "200000", "13:00", "69.99", "70.01"],
                ["BRV6", "71", "0", "100000", "13:00", "70.99", "71.01"],
            ],
        },
    }
    return payload, _report(url)


def test_sync_does_not_drop_root_on_partial_session_turnover(session):
    kept = universe.sync_futures(session, now=NOW, fetch=_partial_session_board)
    assert {item.symbol for item in kept} == {"SiU6", "BRU6"}


def test_admission_uses_completed_h1_days_when_d1_turnover_is_missing(session):
    item = Instrument(
        instrument_id="MOEX:FUT:SiU6",
        venue=Venue.MOEX,
        asset_class=AssetClass.FUTURES,
        symbol="SiU6",
        tick_size=Decimal("1"),
        tick_value=Decimal("1"),
        expiry=NOW.date() + timedelta(days=34),
        next_contract="MOEX:FUT:SiZ6",
        in_universe=True,
    )
    session.add(item)

    # FORTS ISS D1 frequently has an empty/zero VALUE field in production.
    for day in range(30):
        session.add(
            Bar(
                instrument_id=item.instrument_id,
                timeframe=Timeframe.D1,
                open_time=NOW - timedelta(days=day + 1),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume_notional=Decimal("0"),
                open_interest=Decimal("6000000"),
                is_closed=True,
                source="test",
            )
        )

    # 300 closed H1 bars give enough history.  100k contracts * 100 RUB
    # contract value per bar is comfortably above the 100m/day liquidity gate.
    for hour in range(300):
        session.add(
            Bar(
                instrument_id=item.instrument_id,
                timeframe=Timeframe.H1,
                open_time=NOW - timedelta(hours=hour + 24),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume_units=Decimal("100000"),
                is_closed=True,
                source="test",
            )
        )
    session.flush()

    verdict = universe.admit_futures(
        session,
        item,
        now=NOW,
        spread_snapshot=Decimal("0.0001"),
    )

    assert verdict.admitted, verdict.reasons
    assert verdict.measured["turnover_source"] == "completed_h1_30d_median"
