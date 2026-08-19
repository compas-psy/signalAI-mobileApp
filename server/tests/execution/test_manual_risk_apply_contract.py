from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_db
from app.execution.enums import ExecutionLifecycleMode
from app.main import app
from app.models import ExecutionRiskOverride, RiskSnapshot, TradeIdea
from app.risk.manual_preview import (
    ManualRiskPreviewRejected,
    preview_manual_risk,
    verify_manual_risk_preview_token,
)
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


def _preview(client, idea, *, preset_id: str = "BOOST_2") -> dict:
    response = client.post(
        "/api/v1/risk/preview",
        json={
            "idea_id": str(idea.id),
            "preset_id": preset_id,
            "current_mode": "PAPER",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["allowed"] is True
    assert body["preview_hash"]
    return body


def _apply_payload(idea, preview: dict, **overrides) -> dict:
    payload = {
        "idea_id": str(idea.id),
        "preset_id": preview["preset_id"],
        "current_mode": preview["execution_mode"],
        "preview_hash": preview["preview_hash"],
        "owner_confirmed": True,
        "reason": "owner confirmed bounded risk boost",
    }
    payload.update(overrides)
    return payload


def _timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def test_sai_044_apply_rechecks_signed_preview_and_persists_server_owned_values(
    client,
    session,
    instrument,
    now,
):
    idea, risk = _seed(session, instrument, now)
    preview = _preview(client, idea)

    response = client.post(
        "/api/v1/risk/override",
        json=_apply_payload(idea, preview),
        headers={"X-Idempotency-Key": "manual-risk-apply-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["idea_id"] == str(idea.id)
    assert body["risk_snapshot_id"] == str(risk.id)
    assert body["preset_id"] == "BOOST_2"
    assert body["execution_mode"] == "PAPER"
    assert body["venue"] == getattr(instrument.venue, "value", instrument.venue)
    assert body["account"] == "paper-default"
    assert Decimal(str(body["effective_risk_pct"])) == Decimal(
        str(preview["effective_risk_pct"])
    )
    assert Decimal(str(body["effective_quantity"])) == Decimal(str(preview["quantity"]))
    assert body["effective_leverage"] is None

    rows = session.execute(select(ExecutionRiskOverride)).scalars().all()
    assert len(rows) == 1
    override = rows[0]
    assert str(override.id) == body["override_id"]
    assert override.preset == "BOOST_2"
    assert override.preview_hash == hashlib.sha256(
        preview["preview_hash"].encode("utf-8")
    ).hexdigest()
    assert override.detail_json["manual_preview_token_sha256"] == override.preview_hash
    assert _timestamp(override.detail_json["manual_preview_expires_at"]) == _timestamp(
        preview["expires_at"]
    )


def test_sai_044_same_signed_preview_is_single_use_across_idempotency_keys(
    client,
    session,
    instrument,
    now,
):
    idea, _ = _seed(session, instrument, now)
    preview = _preview(client, idea)
    payload = _apply_payload(idea, preview)

    first = client.post(
        "/api/v1/risk/override",
        json=payload,
        headers={"Idempotency-Key": "manual-risk-original"},
    )
    replay = client.post(
        "/api/v1/risk/override",
        json=payload,
        headers={"X-Idempotency-Key": "manual-risk-replay"},
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json()["created"] is True
    assert replay.json()["created"] is False
    assert replay.json()["override_id"] == first.json()["override_id"]
    assert len(session.execute(select(ExecutionRiskOverride)).scalars().all()) == 1


def test_sai_044_rejects_tampered_or_stale_preview_before_persisting(
    client,
    session,
    instrument,
    now,
):
    idea, risk = _seed(session, instrument, now)
    preview = _preview(client, idea)
    token = preview["preview_hash"]
    tampered = f"{token[:-1]}{'0' if token[-1] != '0' else '1'}"

    bad_signature = client.post(
        "/api/v1/risk/override",
        json=_apply_payload(idea, preview, preview_hash=tampered),
        headers={"X-Idempotency-Key": "manual-risk-tampered"},
    )
    assert bad_signature.status_code == 409
    assert session.execute(select(ExecutionRiskOverride)).scalars().all() == []

    risk.open_risk = Decimal("0.001")
    session.flush()
    stale = client.post(
        "/api/v1/risk/override",
        json=_apply_payload(idea, preview),
        headers={"X-Idempotency-Key": "manual-risk-stale"},
    )
    assert stale.status_code == 409
    assert session.execute(select(ExecutionRiskOverride)).scalars().all() == []


def test_sai_044_expired_signed_preview_fails_closed(
    session,
    instrument,
    now,
):
    idea, _ = _seed(session, instrument, now)
    preview = preview_manual_risk(
        session,
        idea_id=idea.id,
        preset_id="BOOST_1",
        current_mode=ExecutionLifecycleMode.PAPER,
        now=now,
    )
    assert preview.preview_hash

    with pytest.raises(ManualRiskPreviewRejected, match="expired"):
        verify_manual_risk_preview_token(
            preview,
            preview.preview_hash,
            now=preview.expires_at + timedelta(seconds=1),
        )

    assert session.execute(select(ExecutionRiskOverride)).scalars().all() == []


def test_sai_044_requires_explicit_owner_confirmation_and_rejects_client_economics(
    client,
    session,
    instrument,
    now,
):
    idea, _ = _seed(session, instrument, now)
    preview = _preview(client, idea)

    not_confirmed = client.post(
        "/api/v1/risk/override",
        json=_apply_payload(idea, preview, owner_confirmed=False),
        headers={"X-Idempotency-Key": "manual-risk-not-confirmed"},
    )
    assert not_confirmed.status_code == 409

    manufactured = _apply_payload(
        idea,
        preview,
        effective_risk_pct="0.99",
        effective_quantity="999",
        leverage="99",
    )
    response = client.post(
        "/api/v1/risk/override",
        json=manufactured,
        headers={"X-Idempotency-Key": "manual-risk-manufactured"},
    )
    assert response.status_code == 422
    assert session.execute(select(ExecutionRiskOverride)).scalars().all() == []


def test_sai_044_auto_preview_is_not_a_risk_increase_and_header_conflicts_fail_closed(
    client,
    session,
    instrument,
    now,
):
    idea, _ = _seed(session, instrument, now)
    preview = _preview(client, idea, preset_id="AUTO")

    auto = client.post(
        "/api/v1/risk/override",
        json=_apply_payload(idea, preview),
        headers={"X-Idempotency-Key": "manual-risk-auto"},
    )
    assert auto.status_code == 409

    boosted = _preview(client, idea, preset_id="BOOST_1")
    conflict = client.post(
        "/api/v1/risk/override",
        json=_apply_payload(idea, boosted),
        headers={
            "Idempotency-Key": "manual-risk-a",
            "X-Idempotency-Key": "manual-risk-b",
        },
    )
    assert conflict.status_code == 409
    assert session.execute(select(ExecutionRiskOverride)).scalars().all() == []
