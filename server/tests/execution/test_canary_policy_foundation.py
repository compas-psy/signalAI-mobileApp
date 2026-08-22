from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import get_db
from app.integration_secrets import BY_SLOT, delete_secret, load_secret, save_secret
from app.main import app
from tests.conftest import DEVICE_HEADERS


LIVE_VALUES = {
    "account_index": "42",
    "api_key_index": "7",
    "api_private_key": "ab" * 32,
}


def _client(session):
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app, headers=DEVICE_HEADERS)


def _policy(generation_id: str, *, markets=(1, 2), instruments=("CRYPTO:PERP:BTCUSDT",)):
    from app.execution.canary_policy import CanaryPolicy

    now = datetime(2026, 8, 22, 7, 0, tzinfo=timezone.utc)
    return CanaryPolicy(
        policy_version="canary-v1",
        source_sha="a" * 40,
        engine_config_hash="b" * 64,
        strategy_family="TREND_PULLBACK",
        strategy_version="trend-pullback-v2",
        credential_generation_id=generation_id,
        account_index=42,
        api_key_index=7,
        market_allowlist=markets,
        instrument_allowlist=instruments,
        capital_amount=Decimal("10000"),
        capital_currency="RUB",
        valuation_source="owner_preapproved",
        valuation_observed_at=now,
        valuation_rule="fixed_preapproved_rub",
        hard_caps={
            "max_order_notional": "2500",
            "max_instrument_notional": "5000",
            "max_gross_notional": "10000",
            "max_open_positions": 2,
            "max_entry_orders": 2,
            "max_leverage": "2",
            "daily_loss_limit": "500",
            "total_loss_limit": "1000",
            "max_order_count": 10,
            "max_trade_count": 5,
        },
        evidence_refs={
            "strategy_performance": "strategy-evidence-1",
            "shadow": "shadow-evidence-1",
            "testnet": "testnet-evidence-1",
            "protection_reconciliation": "protection-evidence-1",
            "kill_switch_drill": "kill-switch-evidence-1",
            "security_scan": "security-scan-1",
            "operational_health": "ops-evidence-1",
        },
        valid_until=now + timedelta(hours=1),
    )


def test_current_mobile_api_cannot_provision_or_delete_lighter_live_trade_secret(session) -> None:
    with _client(session) as client:
        put = client.put(
            "/api/v1/integrations/lighter_trade",
            json={"values": LIVE_VALUES},
        )
        assert put.status_code == 409
        assert put.json()["detail"] == "LIGHTER_LIVE_STEP_UP_REQUIRED"
        assert load_secret(session, "lighter_trade") is None

        delete = client.delete("/api/v1/integrations/lighter_trade")
        assert delete.status_code == 409
        assert delete.json()["detail"] == "LIGHTER_LIVE_STEP_UP_REQUIRED"


def test_invalid_lighter_live_payload_still_fails_validation_before_step_up_gate(session) -> None:
    with _client(session) as client:
        response = client.put(
            "/api/v1/integrations/lighter_trade",
            json={"values": {"account_index": "-1", "api_key_index": "7", "api_private_key": "ab" * 32}},
        )
    assert response.status_code == 422
    assert "account_index" in response.json()["detail"]


def test_low_level_lighter_live_secret_mutation_rotates_opaque_generation_without_secret_derived_metadata(session) -> None:
    from app.execution.canary_policy import current_lighter_trade_generation

    save_secret(session, BY_SLOT["lighter_trade"], LIVE_VALUES, actor="owner_step_up_test")
    first = current_lighter_trade_generation(session)
    assert first is not None
    assert uuid.UUID(first.generation_id)
    assert first.action == "CREATED"
    assert first.account_index == 42
    assert first.api_key_index == 7

    rotated = dict(LIVE_VALUES)
    rotated["api_private_key"] = "cd" * 32
    save_secret(session, BY_SLOT["lighter_trade"], rotated, actor="owner_step_up_test")
    second = current_lighter_trade_generation(session)
    assert second is not None
    assert second.action == "ROTATED"
    assert second.generation_id != first.generation_id

    rows = session.execute(
        text(
            "SELECT generation_id::text, action, actor, account_index, api_key_index "
            "FROM lighter_credential_generations ORDER BY id"
        )
    ).all()
    assert len(rows) == 2
    rendered = repr(rows).lower()
    assert LIVE_VALUES["api_private_key"] not in rendered
    assert rotated["api_private_key"] not in rendered
    assert "fingerprint" not in rendered


def test_lighter_live_runtime_credentials_are_bound_to_current_generation(session) -> None:
    from app.execution.canary_policy import current_lighter_trade_generation
    from app.execution.venues.lighter_auth import (
        LIGHTER_TRADE_SLOT,
        load_lighter_server_credentials,
    )

    save_secret(session, BY_SLOT["lighter_trade"], LIVE_VALUES, actor="owner_step_up_test")
    generation = current_lighter_trade_generation(session)
    credentials = load_lighter_server_credentials(session, LIGHTER_TRADE_SLOT)

    assert generation is not None
    assert credentials is not None
    assert credentials.credential_generation_id == generation.generation_id
    assert credentials.environment == "live"
    assert credentials.purpose == "trade"
    assert credentials.account_index == generation.account_index
    assert credentials.api_key_index == generation.api_key_index


def test_canary_policy_hash_is_deterministic_and_canonicalizes_allowlist_order() -> None:
    from app.execution.canary_policy import canonical_canary_policy

    generation_id = str(uuid.uuid4())
    first = canonical_canary_policy(
        _policy(
            generation_id,
            markets=(2, 1),
            instruments=("CRYPTO:PERP:ETHUSDT", "CRYPTO:PERP:BTCUSDT"),
        )
    )
    second = canonical_canary_policy(
        _policy(
            generation_id,
            markets=(1, 2),
            instruments=("CRYPTO:PERP:BTCUSDT", "CRYPTO:PERP:ETHUSDT"),
        )
    )

    assert first.snapshot_hash == second.snapshot_hash
    assert first.payload["venue"] == "LIGHTER"
    assert first.payload["environment"] == "mainnet"
    assert first.payload["market_allowlist"] == [1, 2]
    assert first.payload["instrument_allowlist"] == [
        "CRYPTO:PERP:BTCUSDT",
        "CRYPTO:PERP:ETHUSDT",
    ]
    assert first.payload["capital_amount"] == "10000"
    assert "api_private_key" not in repr(first.payload)


def test_canary_policy_rejects_duplicate_allowlist_and_missing_evidence() -> None:
    from app.execution.canary_policy import CanaryPolicyError, canonical_canary_policy

    generation_id = str(uuid.uuid4())
    with pytest.raises(CanaryPolicyError, match="market_allowlist"):
        canonical_canary_policy(_policy(generation_id, markets=(1, 1)))

    policy = _policy(generation_id)
    policy.evidence_refs.pop("security_scan")
    with pytest.raises(CanaryPolicyError, match="evidence_refs"):
        canonical_canary_policy(policy)


def test_persisted_canary_snapshot_requires_current_generation_and_is_db_append_only(session) -> None:
    from app.execution.canary_policy import (
        CanaryPolicyError,
        current_lighter_trade_generation,
        persist_canary_policy_snapshot,
    )

    save_secret(session, BY_SLOT["lighter_trade"], LIVE_VALUES, actor="owner_step_up_test")
    generation = current_lighter_trade_generation(session)
    assert generation is not None

    wrong_generation = str(uuid.uuid4())
    with pytest.raises(CanaryPolicyError, match="credential generation"):
        persist_canary_policy_snapshot(
            session,
            _policy(wrong_generation),
            actor="owner_step_up_test",
            correlation_id="canary-preview-1",
        )

    snapshot = persist_canary_policy_snapshot(
        session,
        _policy(generation.generation_id),
        actor="owner_step_up_test",
        correlation_id="canary-preview-1",
    )
    session.flush()
    assert snapshot.snapshot_hash

    with pytest.raises(Exception):
        session.execute(
            text(
                "UPDATE canary_policy_snapshots SET actor='tampered' "
                "WHERE id=:id"
            ),
            {"id": snapshot.id},
        )
        session.flush()
    session.rollback()


def test_delete_records_new_revoked_generation_and_removes_runtime_secret(session) -> None:
    from app.execution.canary_policy import current_lighter_trade_generation

    save_secret(session, BY_SLOT["lighter_trade"], LIVE_VALUES, actor="owner_step_up_test")
    before = current_lighter_trade_generation(session)
    assert before is not None

    delete_secret(session, "lighter_trade", actor="owner_step_up_test")
    after = current_lighter_trade_generation(session, include_revoked=True)
    assert after is not None
    assert after.action == "REVOKED"
    assert after.generation_id != before.generation_id
    assert load_secret(session, "lighter_trade") is None
