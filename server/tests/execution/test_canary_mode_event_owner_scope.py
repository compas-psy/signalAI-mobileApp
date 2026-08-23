from __future__ import annotations

from tests.execution.venues.test_lighter_mainnet_scope_resolver import _snapshot


def test_owner_scope_detail_is_exact_secret_free_and_snapshot_bound(session) -> None:
    from app.execution.canary_activation import build_canary_mode_event_detail

    generation, snapshot = _snapshot(session)
    detail = build_canary_mode_event_detail(snapshot)

    assert detail["canary_policy_snapshot_hash"] == snapshot.snapshot_hash
    assert detail["correlation_id"] == snapshot.correlation_id
    assert detail["source_sha"] == snapshot.source_sha
    assert detail["engine_config_hash"] == snapshot.engine_config_hash
    assert detail["credential_generation_id"] == generation.generation_id
    assert detail["account_index"] == 42
    assert detail["api_key_index"] == 7
    assert detail["strategy_family"] == snapshot.strategy_family
    assert detail["strategy_version"] == snapshot.strategy_version
    assert detail["market_allowlist"] == [1]
    assert detail["instrument_allowlist"] == ["CRYPTO:PERP:BTCUSDT"]
    assert detail["capital_amount"] == "10000"
    assert detail["capital_currency"] == "RUB"
    assert detail["hard_caps"] == snapshot.payload_json["hard_caps"]
    rendered = repr(detail).lower()
    assert "api_private_key" not in rendered
    assert "signed_payload" not in rendered
