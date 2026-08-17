"""Owner FORTS radar exposes persisted production state without rescanning."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import Instrument, PaperTrade, TradeIdea
from app.models.enums import (
    AssetClass,
    Direction,
    IdeaStatus,
    PaperStatus,
    QualityStatus,
    Venue,
)
from tests.conftest import DEVICE_HEADERS, idea_kwargs


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as value:
        yield value
    app.dependency_overrides.clear()


def _forts(
    session,
    *,
    instrument_id: str,
    symbol: str,
    root: str,
    tradable: bool,
    note: str,
    now: datetime,
) -> Instrument:
    item = Instrument(
        instrument_id=instrument_id,
        venue=Venue.MOEX,
        asset_class=AssetClass.FUTURES,
        symbol=symbol,
        title=symbol,
        currency="RUB",
        tick_size=Decimal("1"),
        tick_value=Decimal("1"),
        lot_size=1,
        quantity_step=Decimal("1"),
        min_quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
        expiry=(now + timedelta(days=40)).date(),
        next_contract=f"{instrument_id}:NEXT",
        correlation_cluster="test",
        is_tradable=tradable,
        in_universe=True,
        universe_note=note,
        metadata_json={
            "root": root,
            "snapshot_at": now.isoformat(),
            "snapshot_turnover_rub": "2500000000",
            "snapshot_open_interest": "150000",
            "spread_snapshot": "0.0007",
            "admission": {
                "closed_hourly_bars": "730",
                "days_to_expiry": "40",
                "median_daily_notional_rub": "2200000000",
                "median_oi_notional_rub": "1800000000",
            },
        },
    )
    session.add(item)
    session.flush()
    return item


def test_forts_radar_always_returns_six_core_families_and_rejection_reason(
    client, session
):
    now = datetime.now(UTC)
    _forts(
        session,
        instrument_id="MOEX:FUT:CRU6",
        symbol="CRU6",
        root="CR",
        tradable=False,
        note="не допущен §5: открытый интерес не измерен: history и свежий снимок пусты",
        now=now,
    )

    response = client.get("/api/v1/diagnostics/forts-radar")

    assert response.status_code == 200
    rows = response.json()["roots"]
    assert [row["root"] for row in rows] == ["SI", "CR", "GOLD", "SILV", "BR", "NG"]
    cr = rows[1]
    assert cr["symbol"] == "CRU6"
    assert cr["stage"] == "rejected"
    assert cr["admitted"] is False
    assert cr["primary_reason"].startswith("открытый интерес не измерен")
    assert cr["closed_hourly_bars"] == 730
    assert cr["days_to_expiry"] == 40
    assert rows[0]["stage"] == "not_observed"


def test_forts_radar_distinguishes_admitted_without_setup(client, session):
    now = datetime.now(UTC)
    _forts(
        session,
        instrument_id="MOEX:FUT:BRV6",
        symbol="BRV6",
        root="BR",
        tradable=True,
        note="допущен §5",
        now=now,
    )

    row = next(
        item
        for item in client.get("/api/v1/diagnostics/forts-radar").json()["roots"]
        if item["root"] == "BR"
    )

    assert row["stage"] == "ready_no_setup"
    assert row["primary_reason"] == "допущен, текущего сетапа нет"
    assert row["idea"] is None


def test_forts_radar_links_active_setup_and_server_paper_lifecycle(client, session):
    now = datetime.now(UTC)
    instrument = _forts(
        session,
        instrument_id="MOEX:FUT:SIU6",
        symbol="SIU6",
        root="SI",
        tradable=True,
        note="допущен §5",
        now=now,
    )
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now - timedelta(hours=2),
            status=IdeaStatus.TRIGGERED,
            quality_status=QualityStatus.ACTIVE,
            expires_at=now + timedelta(days=2),
            was_presented=True,
        )
    )
    session.add(idea)
    session.flush()
    trade = PaperTrade(
        idea_id=idea.id,
        instrument_id=instrument.instrument_id,
        direction=Direction.LONG,
        status=PaperStatus.OPEN,
        entry=Decimal("90100"),
        initial_stop=Decimal("89400"),
        tp_prices=["91000", "92000", "93000"],
        tp_shares=["0.4", "0.4", "0.2"],
        current_stop=Decimal("91000"),
        tps_taken=2,
        realized_r=Decimal("1.1"),
        opened_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=2),
        last_reconciled_at=now - timedelta(minutes=3),
    )
    session.add(trade)
    session.flush()

    row = next(
        item
        for item in client.get("/api/v1/diagnostics/forts-radar").json()["roots"]
        if item["root"] == "SI"
    )

    assert row["stage"] == "paper_open"
    assert row["idea"]["id"] == str(idea.id)
    assert row["paper"]["id"] == str(trade.id)
    assert row["paper"]["lifecycle"] == "runner"
    assert row["paper"]["tps_taken"] == 2
    assert row["paper"]["remaining_fraction"] == pytest.approx(0.2)
    assert Decimal(row["paper"]["current_stop"]) == Decimal("91000")
    assert row["paper"]["last_reconciled_at"] is not None
