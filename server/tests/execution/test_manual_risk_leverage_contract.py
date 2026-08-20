from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import Instrument, RiskSnapshot, TradeIdea
from app.models.enums import AssetClass, Venue
from tests.conftest import DEVICE_HEADERS, idea_kwargs


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _crypto_instrument(session, now, *, with_margin_facts: bool) -> Instrument:
    metadata = {}
    if with_margin_facts:
        metadata = {
            "linear_isolated_margin_facts": {
                "source": "bybit-v5-risk-limit",
                "source_ref": "/v5/market/risk-limit",
                "observed_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=5)).isoformat(),
                "venue": "CRYPTO",
                "account": "paper-default",
                "symbol": "BTCUSDT",
                "margin_mode": "ISOLATED",
                "available_margin": "65000",
                "exposure_before": "0",
                "leverage_step": "0.1",
                "tiers": [
                    {
                        "tier_id": 1,
                        "risk_limit_value": "500000",
                        "initial_margin_rate": "0.01",
                        "maintenance_margin_rate": "0.005",
                        "max_leverage": "100",
                        "maintenance_margin_deduction": "0",
                    }
                ],
            }
        }
    item = Instrument(
        instrument_id="CRYPTO:PERP:BTCUSDT",
        venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL,
        symbol="BTCUSDT",
        title="Bitcoin perpetual",
        currency="RUB",
        tick_size=Decimal("1"),
        tick_value=Decimal("1"),
        lot_size=1,
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        min_notional=Decimal("10"),
        contract_multiplier=Decimal("1"),
        correlation_cluster="crypto",
        in_universe=True,
        metadata_json=metadata,
    )
    session.add(item)
    session.flush()
    return item


def _seed(session, instrument, now) -> TradeIdea:
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            status="TRIGGERED",
            quality_status="ACTIVE",
            risk_pct=Decimal("0.005"),
            risk_amount=Decimal("1000"),
            quantity=Decimal("1"),
            correlation_cluster="crypto",
        )
    )
    session.add(idea)
    session.add(
        RiskSnapshot(
            risk_equity=Decimal("200000"),
            open_risk=Decimal("0"),
            day_pnl_pct=Decimal("0"),
            week_pnl_pct=Decimal("0"),
            month_pnl_pct=Decimal("0"),
            current_drawdown=Decimal("0"),
            drawdown_multiplier=Decimal("1"),
            cluster_risk_json={"crypto": "0"},
        )
    )
    session.flush()
    return idea


def test_sai_045_crypto_boost_preview_contains_signed_server_derived_margin_proof(
    client,
    session,
    now,
):
    instrument = _crypto_instrument(session, now, with_margin_facts=True)
    idea = _seed(session, instrument, now)

    response = client.post(
        "/api/v1/risk/preview",
        json={
            "idea_id": str(idea.id),
            "preset_id": "BOOST_2",
            "current_mode": "PAPER",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["allowed"] is True
    assert Decimal(str(body["resulting_leverage"])) == Decimal("3.0")
    assert Decimal(str(body["liquidation_distance_ratio"])) > Decimal("2.5")
    assert "LEVERAGE_LIQUIDATION_DERIVATION_PENDING_SAI_045" not in body["warnings"]
    assert body["preview_hash"]


def test_sai_045_crypto_boost_without_margin_facts_is_visible_but_not_confirmable(
    client,
    session,
    now,
):
    instrument = _crypto_instrument(session, now, with_margin_facts=False)
    idea = _seed(session, instrument, now)

    response = client.post(
        "/api/v1/risk/preview",
        json={
            "idea_id": str(idea.id),
            "preset_id": "BOOST_2",
            "current_mode": "PAPER",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["allowed"] is False
    assert body["resulting_leverage"] is None
    assert body["liquidation_distance_ratio"] is None
    assert "LEVERAGE_LIQUIDATION_BLOCKED:MARGIN_FACTS_MISSING" in body["blockers"]
    assert body["preview_hash"] == ""


def test_sai_045_apply_persists_only_the_server_derived_leverage(
    client,
    session,
    now,
):
    instrument = _crypto_instrument(session, now, with_margin_facts=True)
    idea = _seed(session, instrument, now)
    preview_response = client.post(
        "/api/v1/risk/preview",
        json={
            "idea_id": str(idea.id),
            "preset_id": "BOOST_2",
            "current_mode": "PAPER",
        },
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["allowed"] is True

    response = client.post(
        "/api/v1/risk/override",
        json={
            "idea_id": str(idea.id),
            "preset_id": "BOOST_2",
            "current_mode": "PAPER",
            "preview_hash": preview["preview_hash"],
            "owner_confirmed": True,
            "reason": "owner confirmed bounded crypto risk boost",
        },
        headers={"X-Idempotency-Key": "sai-045-crypto-apply"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert Decimal(str(body["effective_leverage"])) == Decimal("3.0")
