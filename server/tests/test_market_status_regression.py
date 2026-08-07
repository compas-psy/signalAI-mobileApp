from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import Bar, Instrument
from app.models.enums import AssetClass, Timeframe, Venue
from tests.conftest import DEVICE_HEADERS


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as c:
        yield c
    app.dependency_overrides.clear()


def _bar(instrument_id: str, when: datetime) -> Bar:
    return Bar(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        open_time=when,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume_units=Decimal("1000"),
        is_closed=True,
        source="test",
    )


def test_with_data_uses_same_current_universe_as_tradable(
    client, session, instrument
):
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    # Текущий инструмент: именно он входит в знаменатель полноты.
    session.add(_bar(instrument.instrument_id, now - timedelta(hours=1)))

    # Исторический инструмент с данными остаётся в master, но уже вне
    # текущего universe. Раньше его бар увеличивал числитель, поэтому экран
    # мог показывать невозможное «307 из 14».
    old = Instrument(
        instrument_id="CRYPTO:PERP:OLDUSDT",
        venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL,
        symbol="OLDUSDT",
        title="old historical contract",
        currency="USDT",
        tick_size=Decimal("0.01"),
        tick_value=Decimal("0.01"),
        lot_size=1,
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
        is_tradable=True,
        in_universe=False,
        universe_note="вне текущего отбора",
    )
    session.add(old)
    session.flush()
    session.add(_bar(old.instrument_id, now - timedelta(hours=1)))
    session.flush()

    status = client.get("/api/v1/market/status")
    assert status.status_code == 200
    body = status.json()
    assert body["instruments_total"] == 2
    assert body["tradable"] == 1
    assert body["with_data"] == 1
    assert body["with_data"] <= body["tradable"]
