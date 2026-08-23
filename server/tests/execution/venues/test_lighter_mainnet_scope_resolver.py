from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text


def _policy(generation_id: str):
    from app.execution.canary_policy import CanaryPolicy

    now = datetime(2026, 8, 23, 8, 40, tzinfo=UTC)
    return CanaryPolicy(
        policy_version="canary-v1",
        source_sha="a" * 40,
        engine_config_hash="b" * 64,
        strategy_family="TREND_PULLBACK",
        strategy_version="trend-pullback-v2",
        credential_generation_id=generation_id,
        account_index=42,
        api_key_index=7,
        market_allowlist=(1,),
        instrument_allowlist=("CRYPTO:PERP:BTCUSDT",),
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


def _snapshot(session):
    from app.execution.canary_policy import (
        persist_canary_policy_snapshot,
        record_lighter_trade_generation,
    )

    generation = record_lighter_trade_generation(
        session,
        action="CREATED",
        actor="scope-test",
        account_index=42,
        api_key_index=7,
    )
    snapshot = persist_canary_policy_snapshot(
        session,
        _policy(generation.generation_id),
        actor="scope-test",
        correlation_id="scope-test-1",
    )
    session.flush()
    return generation, snapshot


def test_resolver_derives_exact_mainnet_scope_from_snapshot_and_current_generation(session) -> None:
    from app.execution.canary_transport_scope import resolve_lighter_mainnet_canary_scope

    generation, snapshot = _snapshot(session)

    scope = resolve_lighter_mainnet_canary_scope(
        session,
        snapshot_hash=snapshot.snapshot_hash,
    )

    assert scope.snapshot_hash == snapshot.snapshot_hash
    assert scope.credential_generation_id == generation.generation_id
    assert scope.account_index == 42
    assert scope.api_key_index == 7


def test_resolver_rejects_malformed_or_unknown_hash_without_mutation(session) -> None:
    from app.execution.canary_transport_scope import (
        CanaryTransportScopeError,
        resolve_lighter_mainnet_canary_scope,
    )

    for value in ("bad", "f" * 64):
        with pytest.raises(CanaryTransportScopeError):
            resolve_lighter_mainnet_canary_scope(session, snapshot_hash=value)


def test_resolver_rejects_snapshot_after_credential_rotation(session) -> None:
    from app.execution.canary_policy import record_lighter_trade_generation
    from app.execution.canary_transport_scope import (
        CanaryTransportScopeError,
        resolve_lighter_mainnet_canary_scope,
    )

    _generation, snapshot = _snapshot(session)
    record_lighter_trade_generation(
        session,
        action="ROTATED",
        actor="scope-test",
        account_index=42,
        api_key_index=7,
    )
    session.flush()

    with pytest.raises(CanaryTransportScopeError, match="current credential"):
        resolve_lighter_mainnet_canary_scope(
            session,
            snapshot_hash=snapshot.snapshot_hash,
        )


def test_resolver_rejects_snapshot_after_credential_revocation(session) -> None:
    from app.execution.canary_policy import record_lighter_trade_generation
    from app.execution.canary_transport_scope import (
        CanaryTransportScopeError,
        resolve_lighter_mainnet_canary_scope,
    )

    _generation, snapshot = _snapshot(session)
    record_lighter_trade_generation(
        session,
        action="REVOKED",
        actor="scope-test",
        account_index=42,
        api_key_index=7,
    )
    session.flush()

    with pytest.raises(CanaryTransportScopeError, match="current credential"):
        resolve_lighter_mainnet_canary_scope(
            session,
            snapshot_hash=snapshot.snapshot_hash,
        )


def test_resolver_rechecks_canonical_payload_hash_and_row_binding(session) -> None:
    from app.execution.canary_transport_scope import (
        CanaryTransportScopeError,
        resolve_lighter_mainnet_canary_scope,
    )

    generation, snapshot = _snapshot(session)

    # Build a second impossible-but-valid DB row whose claimed hash does not
    # match its payload. The resolver must not trust a 64-hex lookup key alone.
    forged_hash = "c" * 64
    session.execute(
        text(
            """
            INSERT INTO canary_policy_snapshots (
                id, snapshot_hash, schema_version, payload_json, source_sha,
                engine_config_hash, credential_generation_id, account_index,
                api_key_index, strategy_family, strategy_version, valid_until,
                actor, correlation_id
            ) VALUES (
                :id, :snapshot_hash, :schema_version, CAST(:payload AS jsonb),
                :source_sha, :config_hash, CAST(:generation_id AS uuid),
                :account_index, :api_key_index, :strategy_family,
                :strategy_version, :valid_until, :actor, :correlation_id
            )
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "snapshot_hash": forged_hash,
            "schema_version": snapshot.schema_version,
            "payload": '{"venue":"LIGHTER","environment":"mainnet"}',
            "source_sha": snapshot.source_sha,
            "config_hash": snapshot.engine_config_hash,
            "generation_id": generation.generation_id,
            "account_index": 42,
            "api_key_index": 7,
            "strategy_family": snapshot.strategy_family,
            "strategy_version": snapshot.strategy_version,
            "valid_until": snapshot.valid_until,
            "actor": "scope-test",
            "correlation_id": "forged-scope-row",
        },
    )
    session.flush()

    with pytest.raises(CanaryTransportScopeError, match="integrity"):
        resolve_lighter_mainnet_canary_scope(session, snapshot_hash=forged_hash)
