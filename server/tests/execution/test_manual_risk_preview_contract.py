from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import RiskSnapshot, TradeIdea
from tests.conftest import DEVICE_HEADERS, idea_kwargs


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed(session, instrument, now):
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            now,
            status="TRIGGERED",
            quality_status="ACTIVE",
            risk_pct=Decimal("0.005"),
            risk_amount=Decimal("500"),
            quantity=Decimal("1"),
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
            cluster_risk_json={"rub_fx": "0"},
        )
    )
    session.flush()
    return idea


def test_sai_043_preview_is_preset_and_mode_scoped_with_full_economics(
    client,
    session,
    instrument,
    now,
):
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
    assert body["idea_id"] == str(idea.id)
    assert body["preset_id"] == "BOOST_2"
    assert body["execution_mode"] == "PAPER"
    assert body["allowed"] is True
    assert Decimal(str(body["auto_risk_pct"])) == Decimal("0.005")
    assert Decimal(str(body["requested_risk_pct"])) == Decimal("0.0075")
    assert Decimal(str(body["effective_risk_pct"])) == Decimal("0.0075")
    assert Decimal(str(body["auto_risk_amount"])) == Decimal("500")
    assert Decimal(str(body["requested_risk_amount"])) == Decimal("1500")
    assert Decimal(str(body["effective_risk_amount"])) > Decimal("0")
    assert Decimal(str(body["quantity"])) > Decimal("1")
    assert Decimal(str(body["notional"])) > Decimal("0")
    assert Decimal(str(body["total_open_risk_after"])) == Decimal("0.0075")
    assert Decimal(str(body["cluster_risk_after"])) == Decimal("0.0075")
    assert Decimal(str(body["worst_case_stop_loss"])) == Decimal(
        str(body["effective_risk_amount"])
    )
    assert "binding_constraint" in body
    assert "resulting_leverage" in body
    assert "liquidation_distance_ratio" in body
    assert isinstance(body["warnings"], list)
    assert isinstance(body["blockers"], list)

    issued = datetime.fromisoformat(body["issued_at"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    assert issued.tzinfo is not None
    assert expires.tzinfo is not None
    assert int((expires - issued).total_seconds()) == 5 * 60

    version, expires_unix, signature = body["preview_hash"].split(".")
    assert version == "v1"
    assert int(expires_unix) == int(expires.timestamp())
    assert len(signature) == 64
    int(signature, 16)


def test_sai_043_preview_rejects_stale_client_mode(
    client,
    session,
    instrument,
    now,
):
    idea = _seed(session, instrument, now)

    response = client.post(
        "/api/v1/risk/preview",
        json={
            "idea_id": str(idea.id),
            "preset_id": "BOOST_1",
            "current_mode": "SANDBOX",
        },
    )

    assert response.status_code == 409
    assert "execution mode" in str(response.json()).lower()


def test_sai_043_preview_rejects_unknown_or_client_manufactured_preset(
    client,
    session,
    instrument,
    now,
):
    idea = _seed(session, instrument, now)

    response = client.post(
        "/api/v1/risk/preview",
        json={
            "idea_id": str(idea.id),
            "preset_id": "CUSTOM_9X",
            "current_mode": "PAPER",
            "multiplier": "9",
            "risk_pct": "0.99",
            "leverage": "99",
        },
    )

    assert response.status_code == 422


def test_sai_043_boost_preserves_portfolio_caps_and_exposes_binding_constraint(
    client,
    session,
    instrument,
    now,
):
    idea = _seed(session, instrument, now)
    latest = session.query(RiskSnapshot).order_by(RiskSnapshot.taken_at.desc()).first()
    latest.open_risk = Decimal("0.019")
    latest.cluster_risk_json = {"rub_fx": "0.009"}
    session.flush()

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
    assert Decimal(str(body["effective_risk_pct"])) == Decimal("0.001")
    assert body["binding_constraint"] in {"open", "cluster"}
    assert "NO_ADDITIONAL_RISK_HEADROOM" in body["blockers"]
    assert body["preview_hash"] == ""
