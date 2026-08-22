from __future__ import annotations

import contextlib


def test_execution_mode_writer_uses_execution_control_lock(session, monkeypatch) -> None:
    import app.execution.mode as mode_module

    held = False
    entered = 0

    @contextlib.contextmanager
    def fake_lock(_db):
        nonlocal held, entered
        assert held is False
        held = True
        entered += 1
        try:
            yield
        finally:
            held = False

    monkeypatch.setattr(mode_module, "execution_control_lock", fake_lock)
    mode_module.change_execution_mode(
        session,
        target=mode_module.ExecutionLifecycleMode.SANDBOX,
        actor="test",
        reason="serialization contract",
        authorization=mode_module.ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test authorization",
        ),
    )
    assert entered == 1
    assert held is False


def test_lighter_live_credential_writers_use_execution_control_lock(session, monkeypatch) -> None:
    import app.integration_secrets as secrets_module

    held = False
    entered = 0

    @contextlib.contextmanager
    def fake_lock(_db):
        nonlocal held, entered
        assert held is False
        held = True
        entered += 1
        try:
            yield
        finally:
            held = False

    monkeypatch.setattr(secrets_module, "execution_control_lock", fake_lock)
    values = {
        "account_index": "42",
        "api_key_index": "7",
        "api_private_key": "ab" * 32,
    }
    secrets_module.save_secret(
        session,
        secrets_module.BY_SLOT["lighter_trade"],
        values,
        actor="serialization-test",
    )
    secrets_module.delete_secret(session, "lighter_trade", actor="serialization-test")

    assert entered == 2
    assert held is False


def test_non_lighter_credential_write_does_not_take_execution_control_lock(
    session, monkeypatch
) -> None:
    import app.integration_secrets as secrets_module

    @contextlib.contextmanager
    def forbidden_lock(_db):
        raise AssertionError("unrelated integration must not serialize execution")
        yield

    monkeypatch.setattr(secrets_module, "execution_control_lock", forbidden_lock)
    secrets_module.save_secret(
        session,
        secrets_module.BY_SLOT["bybit_read"],
        {"api_key": "read-key", "api_secret": "read-secret"},
        actor="serialization-test",
    )
