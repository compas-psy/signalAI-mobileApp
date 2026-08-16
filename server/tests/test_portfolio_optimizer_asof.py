from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models import Bar, Instrument
from app.models.enums import AssetClass, Timeframe, Venue
from app.portfolio import build

NOW = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)
D = Decimal


def test_daily_closes_excludes_future_bars(session):
    instrument = Instrument(
        instrument_id="MOEX:EQ:ASOF",
        venue=Venue.MOEX,
        asset_class=AssetClass.EQUITY,
        symbol="ASOF",
        title="As-of fixture",
        currency="RUB",
        tick_size=D("0.01"),
        tick_value=D("0.01"),
        lot_size=1,
        quantity_step=D("1"),
        min_quantity=D("1"),
        contract_multiplier=D("1"),
        metadata_json={},
    )
    session.add(instrument)
    session.flush()

    for when, close in (
        (NOW - timedelta(days=30), "90"),
        (NOW - timedelta(days=1), "100"),
        (NOW + timedelta(days=1), "999"),
    ):
        session.add(
            Bar(
                instrument_id=instrument.instrument_id,
                timeframe=Timeframe.D1,
                open_time=when,
                open=D(close),
                high=D(close),
                low=D(close),
                close=D(close),
                volume_units=D("1"),
                volume_notional=D("10000000"),
                is_closed=True,
                source="test",
                quality_flags=[],
            )
        )
    session.flush()

    assert build.daily_closes(
        session,
        [instrument.instrument_id],
        as_of=NOW,
    )[instrument.instrument_id] == [
        ((NOW - timedelta(days=30)).date(), 90.0),
        ((NOW - timedelta(days=1)).date(), 100.0),
    ]
