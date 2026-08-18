from __future__ import annotations

from datetime import UTC, datetime

from app.ops.resources import (
    OllamaMetrics,
    PostgresMetrics,
    RedisMetrics,
    ResourceProbes,
    SystemMetrics,
    collect_resource_snapshot,
)


NOW = datetime(2026, 8, 18, 11, 0, tzinfo=UTC)


def test_resource_snapshot_collects_required_server_signals_without_control_actions():
    probes = ResourceProbes(
        system=lambda: SystemMetrics(
            memory_used_bytes=6_000,
            memory_limit_bytes=8_000,
            swap_used_bytes=500,
            cpu_usage_seconds=321.5,
            load1=1.2,
            load5=0.9,
            load15=0.7,
            disk_used_bytes=40_000,
            disk_total_bytes=100_000,
            inode_used=120,
            inode_total=1_000,
            cgroup_oom_events=2,
            cgroup_oom_kills=1,
        ),
        postgres=lambda now: PostgresMetrics(
            connections=7,
            database_size_bytes=123_456,
            scheduler_lag_seconds=42.0,
            ingest_lag_seconds=180.0,
        ),
        redis=lambda now: RedisMetrics(
            memory_used_bytes=9_000,
            keys=25,
            execution_queue_depth=3,
            execution_queue_lag_seconds=12.0,
        ),
        ollama=lambda: OllamaMetrics(
            reachable=True,
            loaded_models=1,
            configured_model_loaded=True,
        ),
    )

    snapshot = collect_resource_snapshot(now=NOW, probes=probes)

    assert snapshot.system.memory_used_bytes == 6_000
    assert snapshot.system.cgroup_oom_kills == 1
    assert snapshot.postgres.connections == 7
    assert snapshot.postgres.scheduler_lag_seconds == 42.0
    assert snapshot.postgres.ingest_lag_seconds == 180.0
    assert snapshot.redis.execution_queue_depth == 3
    assert snapshot.redis.execution_queue_lag_seconds == 12.0
    assert snapshot.ollama.loaded_models == 1
    assert snapshot.ollama.configured_model_loaded is True
    assert snapshot.probe_errors == ()


def test_resource_snapshot_is_partial_and_fail_open_when_optional_probe_breaks():
    def broken_redis(_now):
        raise ConnectionError("redis unavailable")

    probes = ResourceProbes(
        system=lambda: SystemMetrics.zero(),
        postgres=lambda now: PostgresMetrics.zero(),
        redis=broken_redis,
        ollama=lambda: OllamaMetrics.unavailable(),
    )

    snapshot = collect_resource_snapshot(now=NOW, probes=probes)

    assert snapshot.system == SystemMetrics.zero()
    assert snapshot.redis == RedisMetrics.zero()
    assert snapshot.ollama.reachable is False
    assert snapshot.probe_errors == ("redis:ConnectionError",)
