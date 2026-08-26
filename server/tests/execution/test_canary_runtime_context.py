from __future__ import annotations

from app.config import get_config


def test_runtime_context_uses_only_server_owned_source_config_and_paper_flag(monkeypatch):
    from app.execution.canary_runtime import current_canary_runtime_context

    monkeypatch.setenv("SIGNALAI_SOURCE_SHA", "a" * 40)
    context = current_canary_runtime_context()

    assert context.source_sha == "a" * 40
    assert context.config_hash == get_config().config_hash
    assert context.paper_only is True


def test_runtime_context_fails_closed_when_deployment_source_is_missing(monkeypatch):
    from app.execution.canary_runtime import current_canary_runtime_context

    monkeypatch.delenv("SIGNALAI_SOURCE_SHA", raising=False)
    context = current_canary_runtime_context()

    assert context.source_sha == ""
    assert context.paper_only is True
