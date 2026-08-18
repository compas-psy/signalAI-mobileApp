from __future__ import annotations

from dataclasses import replace

from app.ops.backpressure import (
    BackpressurePlan,
    EntryDisposition,
    WorkloadDisposition,
    WorkloadKind,
    build_backpressure_plan,
)
from app.ops.ollama_shed import (
    OllamaShedStatus,
    shed_ollama_for_plan,
)
from app.ops.pressure import PressureState


def test_normal_plan_never_calls_ollama_transport(monkeypatch):
    monkeypatch.setenv("SIGNALAI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("SIGNALAI_LLM_MODEL", "qwen3.5:4b")
    calls = []

    result = shed_ollama_for_plan(
        build_backpressure_plan(state=PressureState.NORMAL),
        transport=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert result.status is OllamaShedStatus.NOT_REQUIRED
    assert result.attempted is False
    assert calls == []


def test_pressure_plan_unloads_only_configured_model_with_keep_alive_zero(monkeypatch):
    monkeypatch.setenv("SIGNALAI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("SIGNALAI_LLM_MODEL", "qwen3.5:4b")
    calls = []

    def transport(url: str, payload: dict, timeout: float) -> None:
        calls.append((url, payload, timeout))

    result = shed_ollama_for_plan(
        build_backpressure_plan(state=PressureState.PRESSURE),
        transport=transport,
    )

    assert result.status is OllamaShedStatus.UNLOADED
    assert result.attempted is True
    assert result.model == "qwen3.5:4b"
    assert calls == [
        (
            "http://127.0.0.1:11434/api/generate",
            {
                "model": "qwen3.5:4b",
                "prompt": "",
                "stream": False,
                "keep_alive": 0,
            },
            2.0,
        )
    ]


def test_native_base_without_v1_is_preserved(monkeypatch):
    monkeypatch.setenv("SIGNALAI_LLM_BASE_URL", "http://ollama.internal:11434/")
    monkeypatch.setenv("SIGNALAI_LLM_MODEL", "model-a")
    urls = []

    result = shed_ollama_for_plan(
        build_backpressure_plan(state=PressureState.RECOVERING),
        transport=lambda url, payload, timeout: urls.append(url),
    )

    assert result.status is OllamaShedStatus.UNLOADED
    assert urls == ["http://ollama.internal:11434/api/generate"]


def test_missing_base_url_or_model_is_fail_open_without_network(monkeypatch):
    plan = build_backpressure_plan(state=PressureState.PRESSURE)
    calls = []
    monkeypatch.delenv("SIGNALAI_LLM_BASE_URL", raising=False)
    monkeypatch.setenv("SIGNALAI_LLM_MODEL", "qwen3.5:4b")

    no_base = shed_ollama_for_plan(
        plan,
        transport=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    monkeypatch.setenv("SIGNALAI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("SIGNALAI_LLM_MODEL", "   ")
    no_model = shed_ollama_for_plan(
        plan,
        transport=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert no_base.status is OllamaShedStatus.NOT_CONFIGURED
    assert no_model.status is OllamaShedStatus.NOT_CONFIGURED
    assert no_base.attempted is False
    assert no_model.attempted is False
    assert calls == []


def test_transport_failure_is_structured_fail_open(monkeypatch):
    monkeypatch.setenv("SIGNALAI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("SIGNALAI_LLM_MODEL", "qwen3.5:4b")

    def fail(*_args, **_kwargs):
        raise TimeoutError("ollama unavailable")

    result = shed_ollama_for_plan(
        build_backpressure_plan(state=PressureState.CRITICAL),
        transport=fail,
    )

    assert result.status is OllamaShedStatus.FAILED
    assert result.attempted is True
    assert result.model == "qwen3.5:4b"
    assert "TimeoutError" in result.detail


def test_action_obeys_only_ollama_disposition_not_entry_or_protection_state(monkeypatch):
    monkeypatch.setenv("SIGNALAI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("SIGNALAI_LLM_MODEL", "qwen3.5:4b")
    base = build_backpressure_plan(state=PressureState.CRITICAL)
    workloads = dict(base.workloads)
    workloads[WorkloadKind.OLLAMA_EXPLAINABILITY] = WorkloadDisposition.RUN
    plan = BackpressurePlan(
        observed_state=base.observed_state,
        effective_state=base.effective_state,
        workloads=workloads,
        new_entries=EntryDisposition.HALT_NEW_ENTRIES,
        reasons=base.reasons,
    )
    calls = []

    result = shed_ollama_for_plan(
        plan,
        transport=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert result.status is OllamaShedStatus.NOT_REQUIRED
    assert calls == []
    assert plan.workloads[WorkloadKind.POSITION_PROTECTION] is WorkloadDisposition.RUN
    assert plan.workloads[WorkloadKind.EXIT_RECONCILIATION] is WorkloadDisposition.RUN


def test_repeated_shed_requests_are_safe_and_stateless(monkeypatch):
    monkeypatch.setenv("SIGNALAI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("SIGNALAI_LLM_MODEL", "qwen3.5:4b")
    plan = build_backpressure_plan(state=PressureState.PRESSURE)
    calls = []

    def transport(url: str, payload: dict, timeout: float) -> None:
        calls.append((url, payload, timeout))

    first = shed_ollama_for_plan(plan, transport=transport)
    second = shed_ollama_for_plan(plan, transport=transport)

    assert first.status is OllamaShedStatus.UNLOADED
    assert second.status is OllamaShedStatus.UNLOADED
    assert len(calls) == 2
    assert calls[0] == calls[1]
