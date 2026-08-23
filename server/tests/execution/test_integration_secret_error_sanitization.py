from __future__ import annotations

import json

import pytest
from sqlalchemy.exc import StatementError

import app.integration_secrets as secret_store


_LIVE_VALUES = {
    "account_index": "17",
    "api_key_index": "3",
    "api_private_key": "ab" * 32,
}
_SAFE_MESSAGE = "integration secret store unavailable"


def _assert_sanitized(exc: BaseException, *, forbidden: tuple[str, ...]) -> None:
    assert type(exc).__name__ == "IntegrationSecretStoreError"
    assert str(exc) == _SAFE_MESSAGE
    assert exc.__cause__ is None
    assert exc.__context__ is None
    rendered = repr(exc)
    for value in forbidden:
        assert value not in str(exc)
        assert value not in rendered


def test_secret_write_sql_failure_cannot_expose_payload_or_pgcrypto_key(
    session,
    monkeypatch,
) -> None:
    secret_store.ensure_store(session)
    original_execute = session.execute
    raw_payload = json.dumps(_LIVE_VALUES, ensure_ascii=False, sort_keys=True)
    usable_vault_key = secret_store._encryption_key("lighter_trade")

    def failing_execute(statement, params=None, *args, **kwargs):
        if "pgp_sym_encrypt" in str(statement):
            raise StatementError(
                "synthetic secret-bearing write failure",
                str(statement),
                dict(params or {}),
                RuntimeError("provider db failure"),
            )
        return original_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(session, "execute", failing_execute)

    with pytest.raises(Exception) as captured:
        secret_store._save_secret_unlocked(
            session,
            secret_store.BY_SLOT["lighter_trade"],
            dict(_LIVE_VALUES),
            actor="security-test",
        )

    _assert_sanitized(
        captured.value,
        forbidden=(raw_payload, _LIVE_VALUES["api_private_key"], usable_vault_key),
    )


def test_secret_read_sql_failure_cannot_expose_pgcrypto_key(
    session,
    monkeypatch,
) -> None:
    secret_store.ensure_store(session)
    original_execute = session.execute
    usable_vault_key = secret_store._encryption_key("lighter_trade")

    def failing_execute(statement, params=None, *args, **kwargs):
        if "pgp_sym_decrypt" in str(statement):
            raise StatementError(
                "synthetic secret-bearing read failure",
                str(statement),
                dict(params or {}),
                RuntimeError("provider db failure"),
            )
        return original_execute(statement, params, *args, **kwargs)

    monkeypatch.setattr(session, "execute", failing_execute)

    with pytest.raises(Exception) as captured:
        secret_store.load_secret(session, "lighter_trade")

    _assert_sanitized(captured.value, forbidden=(usable_vault_key,))
