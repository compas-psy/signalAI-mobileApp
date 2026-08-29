"""Paper journal money must stay in the instrument quote currency.

R is useful for normalized strategy statistics, but the owner-facing journal
must not turn it into money with today's risk settings.  The immutable idea
sizing records how much quote-currency risk one unit carried at approval time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import Instrument, PaperTrade, TradeIdea
from app.models.enums import AssetClass, Direction, PaperStatus, Venue
from tests.conftest import DEVICE_HEADERS, idea_kwargs


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as c:
        yield c
    app.dependency_overrides.clear()


def _closed_trade(
    session,
    *,
    instrument: Instrument,
    now: datetime,
    quantity: Decimal,
    risk_per_unit: Decimal,
    realized_r: Decimal,
) -> PaperTrade:
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            quantity=quantity,
            risk_per_unit=risk_per_unit,
        )
    )
    session.add(idea)
    session.flush()
    trade = PaperTrade(
        idea_id=idea.id,
        instrument_id=instrument.instrument_id,
        direction=Direction.LONG,
        status=PaperStatus.CLOSED,
        entry=idea.entry_reference,
        initial_stop=idea.stop,
        current_stop=idea.stop,
        tp_prices=[str(idea.tp1), str(idea.tp2), str(idea.tp3)],
        tp_shares=["0.30", "0.30", "0.40"],
        tps_taken=3,
        realized_r=realized_r,
        opened_at=now,
        expires_at=now + timedelta(days=5),
        last_reconciled_at=now + timedelta(hours=6),
        closed_at=now + timedelta(hours=6),
        outcome="TP",
        close_reason="test",
    )
    session.add(trade)
    session.flush()
    return trade


def test_forts_trade_reports_realized_pnl_in_rubles(client, session, instrument, now):
    trade = _closed_trade(
        session,
        instrument=instrument,
        now=now,
        quantity=Decimal("2"),
        risk_per_unit=Decimal("700"),
        realized_r=Decimal("-0.50"),
    )

    rows = client.get("/api/v1/paper/trades?limit=100").json()
    row = next(item for item in rows if item["id"] == str(trade.id))

    assert row["pnl_currency"] == "RUB"
    assert Decimal(row["realized_pnl"]) == Decimal("-700")


def test_crypto_trade_reports_realized_pnl_in_usdt_not_rubles(client, session, now):
    coin = Instrument(
        instrument_id="CRYPTO:PERP:ETHUSDT",
        venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL,
        symbol="ETHUSDT",
        title="Ethereum perpetual",
        currency="USDT",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        in_universe=True,
    )
    session.add(coin)
    session.flush()
    trade = _closed_trade(
        session,
        instrument=coin,
        now=now,
        quantity=Decimal("0.348"),
        risk_per_unit=Decimal("20.16"),
        realized_r=Decimal("1.50"),
    )

    rows = client.get("/api/v1/paper/trades?limit=100").json()
    row = next(item for item in rows if item["id"] == str(trade.id))

    assert row["pnl_currency"] == "USDT"
    assert Decimal(row["realized_pnl"]) == Decimal("10.523520")
