from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.ops.pressure import (
    PressureClassifier,
    PressureContext,
    PressurePolicy,
    PressureState,
)
from app.ops.resources import (
    OllamaMetrics,
    PostgresMetrics,
    RedisMetrics,
    ResourceSnapshot,
    SystemMetrics,
)


T0 = datetime(2026, 8, 18, 16, 0, tzinfo=UTC)
GB = 1024**3


def _snapshot(
    at: datetime,
    *,
    memory_ratio: float = 0.50,
    disk_ratio: float = 0.40,
    inode_ratio: float = 0.30,
    swap_used: int = 0,
    scheduler_lag: float = 5.0,
    queue_depth: int = 0,
    queue_lag: float = 0.0,
    oom_events: int = 0,
    oom_kills: int = 0,
    probe_errors: tuple[str, ...] = (),
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
            load1=0.5,
            load5=0.4,
            load15=0.3,
            disk_used_bytes=int(disk_total * disk_ratio),
            disk_total_bytes=disk_total,
            inode_used=int(inode_total * inode_ratio),
            inode_total=inode_total,
            cgroup_oom_events=oom_events,
            cgroup_oom_kills=oom_kills,
        ),
        postgres=PostgresMetrics(
            connections=5,
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
            loaded_models=1,
            configured_model_loaded=True,
        ),
        probe_errors=probe_errors,
    )


def test_healthy_snapshot_is_normal_and_context_alone_never_creates_pressure():
    classifier = PressureClassifier()

    result = classifier.evaluate(
        _snapshot(T0),
        context=PressureContext(execution_mode="LIVE", open_positions=3),
    )

    assert result.state is PressureState.NORMAL
    assert result.score == 0
    assert result.reasons == ()


def test_single_marginal_headroom_signal_is_watch_not_pressure():
    classifier = PressureClassifier()

    result = classifier.evaluate(_snapshot(T0, memory_ratio=0.81))

    assert result.state is PressureState.WATCH
    assert "memory_headroom_watch" in result.reasons
    assert result.score > 0


def test_multiple_independent_signals_and_worsening_trend_raise_pressure():
    classifier = PressureClassifier()
    classifier.evaluate(
        _snapshot(
            T0,
            memory_ratio=0.80,
            disk_ratio=0.84,
            scheduler_lag=20.0,
        )
    )

    result = classifier.evaluate(
        _snapshot(
            T0 + timedelta(minutes=2),
            memory_ratio=0.88,
            disk_ratio=0.92,
            scheduler_lag=180.0,
            queue_depth=4,
            queue_lag=45.0,
        ),
        context=PressureContext(execution_mode="PAPER", open_positions=1),
    )

    assert result.state is PressureState.PRESSURE
    assert "memory_headroom_pressure" in result.reasons
    assert "disk_headroom_pressure" in result.reasons
    assert "resource_trend_worsening" in result.reasons
    assert "scheduler_lag" in result.reasons
    assert "execution_queue_lag" in result.reasons
    assert result.active_dimensions >= 3


def test_oom_kill_is_immediate_critical_even_if_other_headroom_looks_healthy():
    classifier = PressureClassifier()
    classifier.evaluate(_snapshot(T0, oom_events=1, oom_kills=0))

    result = classifier.evaluate(
        _snapshot(
            T0 + timedelta(seconds=10),
            memory_ratio=0.55,
            oom_events=2,
            oom_kills=1,
        ),
        context=PressureContext(execution_mode="PAPER", open_positions=2),
    )

    assert result.state is PressureState.CRITICAL
    assert "oom_kill_detected" in result.reasons


def test_execution_context_increases_sensitivity_only_when_resource_risk_exists():
    inactive = PressureClassifier()
    active = PressureClassifier()
    sample = _snapshot(
        T0,
        memory_ratio=0.86,
        queue_depth=2,
        queue_lag=35.0,
    )

    inactive_result = inactive.evaluate(
        sample,
        context=PressureContext(execution_mode="PAPER", open_positions=0),
    )
    active_result = active.evaluate(
        sample,
        context=PressureContext(execution_mode="CANARY_LIVE", open_positions=1),
    )

    assert inactive_result.state in {PressureState.WATCH, PressureState.PRESSURE}
    assert active_result.score > inactive_result.score
    assert "active_execution_context" in active_result.reasons


def test_probe_failure_is_visible_but_never_critical_by_itself():
    classifier = PressureClassifier()

    result = classifier.evaluate(
        _snapshot(T0, probe_errors=("redis:ConnectionError",))
    )

    assert result.state is PressureState.WATCH
    assert "resource_probe_degraded" in result.reasons
    assert result.state is not PressureState.CRITICAL


def test_recovery_uses_hysteresis_and_requires_full_healthy_window():
    policy = PressurePolicy(recovery_window=timedelta(minutes=15))
    classifier = PressureClassifier(policy=policy)

    pressured = classifier.evaluate(
        _snapshot(
            T0,
            memory_ratio=0.90,
            disk_ratio=0.93,
            scheduler_lag=180.0,
            queue_depth=3,
            queue_lag=60.0,
        )
    )
    assert pressured.state is PressureState.PRESSURE

    first_healthy = classifier.evaluate(_snapshot(T0 + timedelta(minutes=1)))
    assert first_healthy.state is PressureState.RECOVERING

    still_recovering = classifier.evaluate(_snapshot(T0 + timedelta(minutes=15)))
    assert still_recovering.state is PressureState.RECOVERING

    recovered = classifier.evaluate(_snapshot(T0 + timedelta(minutes=16)))
    assert recovered.state is PressureState.NORMAL


def test_renewed_pressure_during_recovery_cancels_recovery_immediately():
    classifier = PressureClassifier()
    classifier.evaluate(
        _snapshot(
            T0,
            memory_ratio=0.90,
            disk_ratio=0.93,
            scheduler_lag=180.0,
        )
    )
    assert classifier.evaluate(
        _snapshot(T0 + timedelta(minutes=1))
    ).state is PressureState.RECOVERING

    renewed = classifier.evaluate(
        _snapshot(
            T0 + timedelta(minutes=2),
            memory_ratio=0.91,
            disk_ratio=0.94,
            scheduler_lag=180.0,
        )
    )

    assert renewed.state is PressureState.PRESSURE


def test_invalid_or_zero_capacity_is_ignored_instead_of_dividing_by_zero():
    classifier = PressureClassifier()
    sample = _snapshot(T0)
    sample = ResourceSnapshot(
        collected_at=sample.collected_at,
        system=SystemMetrics(
            memory_used_bytes=sample.system.memory_used_bytes,
            memory_limit_bytes=0,
            swap_used_bytes=0,
            cpu_usage_seconds=sample.system.cpu_usage_seconds,
            load1=sample.system.load1,
            load5=sample.system.load5,
            load15=sample.system.load15,
            disk_used_bytes=sample.system.disk_used_bytes,
            disk_total_bytes=0,
            inode_used=sample.system.inode_used,
            inode_total=0,
            cgroup_oom_events=0,
            cgroup_oom_kills=0,
        ),
        postgres=sample.postgres,
        redis=sample.redis,
        ollama=sample.ollama,
        probe_errors=(),
    )

    result = classifier.evaluate(sample)

    assert result.state is PressureState.NORMAL
