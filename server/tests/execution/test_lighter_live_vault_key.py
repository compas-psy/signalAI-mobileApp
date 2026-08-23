from __future__ import annotations

import pytest

from app.integration_secrets import BY_SLOT, load_secret, save_secret


LIVE_VALUES = {
    "account_index": "42",
    "api_key_index": "7",
    "api_private_key": "ab" * 32,
}


def test_lighter_live_secret_rejects_generic_or_database_derived_vault_key(
    session,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SIGNALAI_LIGHTER_LIVE_SECRETS_KEY", raising=False)
    monkeypatch.setenv("SIGNALAI_SECRETS_KEY", "generic-test-vault-key-material-1234567890")

    with pytest.raises(RuntimeError, match="SIGNALAI_LIGHTER_LIVE_SECRETS_KEY"):
        save_secret(
            session,
            BY_SLOT["lighter_trade"],
            LIVE_VALUES,
            actor="vault-boundary-test",
        )


def test_lighter_live_secret_uses_only_dedicated_vault_key(
    session,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "SIGNALAI_LIGHTER_LIVE_SECRETS_KEY",
        "lighter-live-dedicated-key-material-1234567890",
    )
    monkeypatch.setenv("SIGNALAI_SECRETS_KEY", "generic-key-material-before-123456789012")

    save_secret(
        session,
        BY_SLOT["lighter_trade"],
        LIVE_VALUES,
        actor="vault-boundary-test",
    )

    monkeypatch.setenv("SIGNALAI_SECRETS_KEY", "generic-key-material-after-1234567890123")
    assert load_secret(session, "lighter_trade") == LIVE_VALUES


def test_non_live_integration_secret_keeps_existing_generic_vault_behavior(
    session,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SIGNALAI_LIGHTER_LIVE_SECRETS_KEY", raising=False)
    monkeypatch.setenv("SIGNALAI_SECRETS_KEY", "generic-test-vault-key-material-1234567890")
    values = {"api_key": "read-key", "api_secret": "read-secret"}

    save_secret(
        session,
        BY_SLOT["bybit_read"],
        values,
        actor="vault-boundary-test",
    )

    assert load_secret(session, "bybit_read") == values
