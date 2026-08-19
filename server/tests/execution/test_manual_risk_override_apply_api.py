from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_db
from app.execution.enums import ExecutionLifecycleMode
from app.execution.mode import ModeChangeAuthorization, change_execution_mode
from app.main import app
from app.models import ExecutionRiskOverride, RiskSnapshot, TradeIdea
from app.risk.manual_preview import preview_manual_risk
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
            risk_amount=Decimal("1000"),
            quantity=Decimal("1"),
        )
    )
    risk = RiskSnapshot(
        risk_equity=Decimal("200000"),
        open_risk=Decimal("0"),
        day_pnl_pct=Decimal("0"),
        week_pnl_pct=Decimal("0"),
        month_pnl_pct=Decimal("0"),
        current_drawdown=Decimal("0"),
        drawdown_multiplier=Decimal("1"),
        cluster_risk_json={"rub_fx": "0"},
    )
    session.add_all([idea, risk])
    session.flush()
    return idea, risk


def _preview(client, idea, preset="BOOST_2"):
    response = client.post(
        "/api/v1/risk/preview",
        json={
            "idea_id": str(idea.id),
            "preset_id": preset,
            "current_mode": "PAPER",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["allowed"] is True
    assert body["preview_hash"]
    return body


def _apply(client, idea, shown, *, key="manual-risk-apply-1", confirmed=True):
    return client.post(
        "/api/v1/risk/override",
        headers={"X-Idempotency-Key": key},
        json={
            "idea_id": str(idea.id),
            "preset_id": shown["preset_id"],
            "preview_hash": shown["preview_hash"],
            "owner_confirmed": confirmed,
        },
    )


def test_sai_044_applies_exact_signed_preview_without_client_economics(
    client,
    session,
    instrument,
    now,
):
    idea, risk = _seed(session, instrument, now)
    shown = _preview(client, idea)

    response = _apply(client, idea, shown)

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["idea_id"] == str(idea.id)
    assert body["preset_id"] == "BOOST_2"
    assert body["execution_mode"] == "PAPER"
    assert body["preview_hash"] == shown["preview_hash"]
    assert Decimal(str(body["effective_risk_pct"])) == Decimal("0.0075")
    assert Decimal(str(body["effective_quantity"])) == Decimal(str(shown["quantity"]))

    override = session.execute(select(ExecutionRiskOverride)).scalar_one()
    assert override.idea_id == idea.id
    assert override.risk_snapshot_id == risk.id
    assert override.preset == "BOOST_2"
    assert override.execution_mode_snapshot == ExecutionLifecycleMode.PAPER
    assert override.preview_hash == shown["preview_hash"]
    assert len(override.preview_hash) > 64
    # SAI-044's public contract does not let the phone manufacture execution
    # scope. Venue/account are bound later by the execution intent/provider.
    assert override.venue is None
    assert override.account is None


def test_sai_044_rejects_client_supplied_risk_quantity_leverage_or_scope(
    client,
    session,
    instrument,
    now,
):
    idea, _ = _seed(session, instrument, now)
    shown = _preview(client, idea)

    response = client.post(
        "/api/v1/risk/override",
        headers={"X-Idempotency-Key": "manual-risk-extra-fields"},
        json={
            "idea_id": str(idea.id),
            "preset_id": "BOOST_2",
            "preview_hash": shown["preview_hash"],
            "owner_confirmed": True,
            "effective_risk_pct": "0.99",
            "effective_quantity": "999999",
            "effective_leverage": "100",
            "venue": "ATTACKER",
            "account": "ATTACKER",
        },
    )

    assert response.status_code == 422
    assert session.execute(select(ExecutionRiskOverride)).scalars().all() == []


def test_sai_044_requires_explicit_confirmation_and_idempotency_key(
    client,
    session,
    instrument,
    now,
):
    idea, _ = _seed(session, instrument, now)
    shown = _preview(client, idea)

    not_confirmed = _apply(
        client,
        idea,
        shown,
        key="manual-risk-not-confirmed",
        confirmed=False,
    )
    no_key = client.post(
        "/api/v1/risk/override",
        json={
            "idea_id": str(idea.id),
            "preset_id": shown["preset_id"],
            "preview_hash": shown["preview_hash"],
            "owner_confirmed": True,
        },
    )

    assert not_confirmed.status_code == 409
    assert no_key.status_code == 422
    assert session.execute(select(ExecutionRiskOverride)).scalars().all() == []


def test_sai_044_rejects_expired_signed_preview(
    client,
    session,
    instrument,
    now,
):
    idea, _ = _seed(session, instrument, now)
    old = preview_manual_risk(
        session,
        idea_id=idea.id,
        preset_id="BOOST_2",
        current_mode=ExecutionLifecycleMode.PAPER,
        now=datetime.now(UTC) - timedelta(minutes=10),
    )
    assert old.preview_hash

    response = client.post(
        "/api/v1/risk/override",
        headers={"X-Idempotency-Key": "manual-risk-expired"},
        json={
            "idea_id": str(idea.id),
            "preset_id": "BOOST_2",
            "preview_hash": old.preview_hash,
            "owner_confirmed": True,
        },
    )

    assert response.status_code == 409
    assert "expired" in str(response.json()).lower()
    assert session.execute(select(ExecutionRiskOverride)).scalars().all() == []


def test_sai_044_rejects_preview_when_risk_state_changed_after_display(
    client,
    session,
    instrument,
    now,
):
    idea, risk = _seed(session, instrument, now)
    shown = _preview(client, idea)
    risk.open_risk = Decimal("0.019")
    risk.cluster_risk_json = {"rub_fx": "0.009"}
    session.flush()

    response = _apply(client, idea, shown, key="manual-risk-stale-risk")

    assert response.status_code == 409
    assert "stale" in str(response.json()).lower()
    assert session.execute(select(ExecutionRiskOverride)).scalars().all() == []


def test_sai_044_rejects_preview_when_execution_mode_changed_after_display(
    client,
    session,
    instrument,
    now,
):
    idea, _ = _seed(session, instrument, now)
    shown = _preview(client, idea)
    change_execution_mode(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
        actor="owner",
        reason="test promotion",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test-only proof",
        ),
    )

    response = _apply(client, idea, shown, key="manual-risk-stale-mode")

    assert response.status_code == 409
    assert "stale" in str(response.json()).lower()
    assert session.execute(select(ExecutionRiskOverride)).scalars().all() == []


def test_sai_044_is_idempotent_only_for_the_same_preview_and_key(
    client,
    session,
    instrument,
    now,
):
    idea, _ = _seed(session, instrument, now)
    shown = _preview(client, idea)

    first = _apply(client, idea, shown, key="manual-risk-repeat")
    second = _apply(client, idea, shown, key="manual-risk-repeat")
    replay_with_other_key = _apply(
        client,
        idea,
        shown,
        key="manual-risk-other-key",
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["risk_override_id"] == second.json()["risk_override_id"]
    assert replay_with_other_key.status_code == 409
    assert session.execute(select(ExecutionRiskOverride)).scalars().all().__len__() == 1
