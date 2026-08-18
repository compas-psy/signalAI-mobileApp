from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.ops.backpressure import (
    BackpressureContext,
    EntryDisposition,
    WorkloadDisposition,
    WorkloadKind,
    build_backpressure_plan,
)
from app.ops.ollama_shed import OllamaShedStatus, shed_ollama_for_plan
from app.ops.pressure import PressureClassifier, PressureContext, PressurePolicy, PressureState
from app.ops.resources import (
    OllamaMetrics,
    PostgresMetrics,
    RedisMetrics,
    ResourceSnapshot,
    SystemMetrics,
)


T0 = datetime(2026, 8, 18, 18, 30, tzinfo=UTC)
GB = 1024**3


def _snapshot(
    at: datetime,
    *,
    memory_ratio: float = 0.50,
    disk_ratio: float = 0.40,
    swap_used: int = 0,
    postgres_connections: int = 5,
    scheduler_lag: float = 5.0,
    queue_depth: int = 0,
    queue_lag: float = 0.0,
    ollama_loaded: bool = True,
) -> ResourceSnapshot:
    memory_limit = 8 * GB
    disk_total = 100 * GB
    inode_total = 100_000
    return ResourceSnapshot(
        collected_at=at,
        system=SystemMetrics(
            memory_used_bytes=int(memory_limit * memory_ratio),
            memory_limit_bytes=memory_limit,
            swap_used_bytes=swap_used,
            cpu_usage_seconds=100.0,
            load1=0.7,
            load5=0.5,
            load15=0.4,
            disk_used_bytes=int(disk_total * disk_ratio),
            disk_total_bytes=disk_total,
            inode_used=30_000,
            inode_total=inode_total,
            cgroup_oom_events=0,
            cgroup_oom_kills=0,
        ),
        postgres=PostgresMetrics(
            connections=postgres_connections,
            database_size_bytes=GB,
            scheduler_lag_seconds=scheduler_lag,
            ingest_lag_seconds=30.0,
        ),
        redis=RedisMetrics(
            memory_used_bytes=64 * 1024**2,
            keys=20,
            execution_queue_depth=queue_depth,
            execution_queue_lag_seconds=queue_lag,
        ),
        ollama=OllamaMetrics(
            reachable=True,
            loaded_models=1 if ollama_loaded else 0,
            configured_model_loaded=ollama_loaded,
        ),
        probe_errors=(),
    )


def _evaluate(
    snapshot: ResourceSnapshot,
    *,
    classifier: PressureClassifier | None = None,
    open_positions: int = 1,
):
    classifier = classifier or PressureClassifier()
    assessment = classifier.evaluate(
        snapshot,
        context=PressureContext(execution_mode="PAPER", open_positions=open_positions),
    )
    plan = build_backpressure_plan(
        state=assessment.state,
        context=BackpressureContext(execution_mode="PAPER", open_positions=open_positions),
    )
    return assessment, plan


def _assert_execution_preserved(plan) -> None:
    assert plan.workloads[WorkloadKind.POSITION_PROTECTION] is WorkloadDisposition.RUN
    assert plan.workloads[WorkloadKind.EXIT_RECONCILIATION] is WorkloadDisposition.RUN
    assert plan.workloads[WorkloadKind.SIGNAL_PIPELINE] is WorkloadDisposition.RUN


def test_memory_90_percent_with_execution_queue_sheds_optional_work_not_execution(monkeypatch):
    assessment, plan = _evaluate(
        _snapshot(
            T0,
            memory_ratio=0.90,
            queue_depth=2,
            queue_lag=35.0,
            ollama_loaded=True,
        )
    )

    assert assessment.state is PressureState.PRESSURE
    _assert_execution_preserved(plan)
    assert plan.new_entries is EntryDisposition.ALLOW
    assert plan.workloads[WorkloadKind.OLLAMA_EXPLAINABILITY] is WorkloadDisposition.SHED
    assert plan.workloads[WorkloadKind.RESEARCH_BACKTEST_REPLAY] is WorkloadDisposition.PAUSE

    calls: list[tuple[str, dict, float]] = []
    monkeypatch.setenv("SIGNALAI_LLM_BASE_URL", "http://ollama:11434")
    monkeypatch.setenv("SIGNALAI_LLM_MODEL", "qwen3.5:4b")
    result = shed_ollama_for_plan(
        plan,
        transport=lambda url, payload, timeout: calls.append((url, payload, timeout)),
    )

    assert result.status is OllamaShedStatus.UNLOADED
    assert len(calls) == 1
    _assert_execution_preserved(plan)


def test_disk_rapidly_filling_escalates_without_starving_execution():
    classifier = PressureClassifier()
    classifier.evaluate(
        _snapshot(T0, disk_ratio=0.84),
        context=PressureContext(execution_mode="PAPER", open_positions=1),
    )

    assessment, plan = _evaluate(
        _snapshot(T0 + timedelta(minutes=2), disk_ratio=0.94),
        classifier=classifier,
    )

    assert assessment.state is PressureState.PRESSURE
    assert "disk_headroom_pressure" in assessment.reasons
    assert "resource_trend_worsening" in assessment.reasons
    _assert_execution_preserved(plan)


def test_redis_execution_queue_growth_preserves_protection_and_reconciliation():
    assessment, plan = _evaluate(
        _snapshot(T0, queue_depth=12, queue_lag=180.0)
    )

    assert assessment.state is PressureState.PRESSURE
    assert "execution_queue_depth" in assessment.reasons
    assert "execution_queue_lag" in assessment.reasons
    _assert_execution_preserved(plan)
    assert plan.workloads[WorkloadKind.RESEARCH_BACKTEST_REPLAY] is WorkloadDisposition.PAUSE


def test_postgres_connection_saturation_is_configurable_pressure_signal():
    policy = PressurePolicy(
        postgres_connections_watch=40,
        postgres_connections_pressure=80,
    )
    classifier = PressureClassifier(policy=policy)

    assessment, plan = _evaluate(
        _snapshot(
            T0,
            postgres_connections=85,
            scheduler_lag=75.0,
            queue_depth=1,
        ),
        classifier=classifier,
    )

    assert assessment.state is PressureState.PRESSURE
    assert "postgres_connections_pressure" in assessment.reasons
    _assert_execution_preserved(plan)


def test_scheduler_runaway_is_throttled_before_execution_work():
    assessment, plan = _evaluate(
        _snapshot(T0, scheduler_lag=240.0, queue_depth=2, queue_lag=45.0)
    )

    assert assessment.state is PressureState.PRESSURE
    assert "scheduler_lag" in assessment.reasons
    _assert_execution_preserved(plan)
    assert plan.workloads[WorkloadKind.TELEGRAM_UI] is WorkloadDisposition.THROTTLE
    assert plan.workloads[WorkloadKind.RESEARCH_BACKTEST_REPLAY] is WorkloadDisposition.PAUSE


def test_critical_pressure_halts_only_new_entries_while_nonzero_execution_queue_keeps_running():
    assessment, plan = _evaluate(
        _snapshot(
            T0,
            memory_ratio=0.96,
            scheduler_lag=180.0,
            queue_depth=10,
            queue_lag=180.0,
        )
    )

    assert assessment.state is PressureState.CRITICAL
    assert plan.new_entries is EntryDisposition.HALT_NEW_ENTRIES
    _assert_execution_preserved(plan)
    assert plan.workloads[WorkloadKind.OLLAMA_EXPLAINABILITY] is WorkloadDisposition.SHED


def test_ollama_unload_failure_is_fail_open_for_execution(monkeypatch):
    _, plan = _evaluate(
        _snapshot(T0, memory_ratio=0.90, queue_depth=2, queue_lag=35.0)
    )
    monkeypatch.setenv("SIGNALAI_LLM_BASE_URL", "http://ollama:11434")
    monkeypatch.setenv("SIGNALAI_LLM_MODEL", "qwen3.5:4b")

    def fail_transport(url: str, payload: dict, timeout: float) -> None:
        raise TimeoutError("simulated Ollama stall")

    result = shed_ollama_for_plan(plan, transport=fail_transport)

    assert result.status is OllamaShedStatus.FAILED
    assert result.attempted is True
    _assert_execution_preserved(plan)


def test_postgres_saturation_thresholds_must_be_both_set_and_ordered():
    with pytest.raises(ValueError, match="postgres connection thresholds"):
        PressurePolicy(postgres_connections_watch=40)
    with pytest.raises(ValueError, match="postgres connection thresholds"):
        PressurePolicy(
            postgres_connections_watch=80,
            postgres_connections_pressure=40,
        )
