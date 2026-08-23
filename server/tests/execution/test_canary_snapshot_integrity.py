from __future__ import annotations

from tests.execution.venues.test_lighter_mainnet_scope_resolver import _snapshot


def test_persisted_snapshot_integrity_returns_exact_canonical_payload(session) -> None:
    from app.execution.canary_policy import verify_persisted_canary_snapshot

    _generation, snapshot = _snapshot(session)

    payload = verify_persisted_canary_snapshot(snapshot)

    assert payload["venue"] == "LIGHTER"
    assert payload["environment"] == "mainnet"
    assert payload["credential_generation_id"] == str(snapshot.credential_generation_id)
    assert payload["account_index"] == snapshot.account_index
    assert payload["api_key_index"] == snapshot.api_key_index
