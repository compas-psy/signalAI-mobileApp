from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import ExecutionRiskOverride, RiskSnapshot, TradeIdea
from tests.conftest import DEVICE_HEADERS, idea_kwargs


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_idea(session, instrument, now):
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


def test_risk_on_preview_exposes_only_server_calculated_economics(
    client,
    session,
    instrument,
    now,
):
    idea = _seed_idea(session, instrument, now)

    response = client.post(
        "/api/v1/execution/risk-on/preview",
        json={
            "idea_id": str(idea.id),
            "venue": "TINVEST",
            "account": "sandbox-main",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["idea_id"] == str(idea.id)
    assert body["venue"] == "TINVEST"
    assert body["account"] == "sandbox-main"
    assert body["allowed"] is True
    assert Decimal(str(body["base_risk_pct"])) == Decimal("0.005")
    assert Decimal(str(body["effective_risk_pct"])) == Decimal("0.0075")
    assert Decimal(str(body["hard_cap_risk_pct"])) == Decimal("0.0075")
    assert Decimal(str(body["base_quantity"])) == Decimal("1")
    assert Decimal(str(body["effective_quantity"])) == Decimal("2")
    assert body["effective_leverage"] is None
    assert Decimal(str(body["hard_cap_leverage"])) == Decimal("3.0")
    assert len(body["preview_hash"]) == 64


def test_risk_on_preview_rejects_mobile_supplied_risk_quantity_or_leverage(
    client,
    session,
    instrument,
    now,
):
    idea = _seed_idea(session, instrument, now)

    response = client.post(
        "/api/v1/execution/risk-on/preview",
        json={
            "idea_id": str(idea.id),
            "venue": "TINVEST",
            "account": "sandbox-main",
            "effective_risk_pct": "0.99",
            "effective_quantity": "999999",
            "effective_leverage": "100",
        },
    )

    assert response.status_code == 422
    assert session.query(ExecutionRiskOverride).count() == 0


def test_risk_on_confirm_uses_mobile_idempotency_header_and_persists_exact_preview(
    client,
    session,
    instrument,
    now,
):
    idea = _seed_idea(session, instrument, now)
    preview = client.post(
        "/api/v1/execution/risk-on/preview",
        json={
            "idea_id": str(idea.id),
            "venue": "TINVEST",
            "account": "sandbox-main",
        },
    )
    assert preview.status_code == 200
    shown = preview.json()

    payload = {
        "idea_id": str(idea.id),
        "venue": "TINVEST",
        "account": "sandbox-main",
        "preview_hash": shown["preview_hash"],
        "owner_confirmed": True,
    }
    first = client.post(
        "/api/v1/execution/risk-on/confirm",
        headers={"X-Idempotency-Key": "risk-on-mobile-1"},
        json=payload,
    )
    second = client.post(
        "/api/v1/execution/risk-on/confirm",
        headers={"X-Idempotency-Key": "risk-on-mobile-1"},
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["created"] is True
    assert second_body["created"] is False
    assert first_body["risk_override_id"] == second_body["risk_override_id"]
    assert first_body["preview_hash"] == shown["preview_hash"]
    assert Decimal(str(first_body["effective_risk_pct"])) == Decimal("0.0075")
    assert Decimal(str(first_body["effective_quantity"])) == Decimal("2")
    assert first_body["effective_leverage"] is None
    assert session.query(ExecutionRiskOverride).count() == 1


def test_risk_on_confirm_requires_explicit_confirmation_and_idempotency_key(
    client,
    session,
    instrument,
    now,
):
    idea = _seed_idea(session, instrument, now)
    preview = client.post(
        "/api/v1/execution/risk-on/preview",
        json={
            "idea_id": str(idea.id),
            "venue": "TINVEST",
            "account": "sandbox-main",
        },
    )
    assert preview.status_code == 200
    shown = preview.json()

    without_owner = client.post(
        "/api/v1/execution/risk-on/confirm",
        headers={"X-Idempotency-Key": "risk-on-mobile-2"},
        json={
            "idea_id": str(idea.id),
            "venue": "TINVEST",
            "account": "sandbox-main",
            "preview_hash": shown["preview_hash"],
            "owner_confirmed": False,
        },
    )
    without_key = client.post(
        "/api/v1/execution/risk-on/confirm",
        json={
            "idea_id": str(idea.id),
            "venue": "TINVEST",
            "account": "sandbox-main",
            "preview_hash": shown["preview_hash"],
            "owner_confirmed": True,
        },
    )

    assert without_owner.status_code == 409
    assert without_key.status_code == 422
    assert session.query(ExecutionRiskOverride).count() == 0
