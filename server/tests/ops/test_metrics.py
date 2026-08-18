from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.main import app
from app.ops.metrics import render_metrics, reset_http_metrics_for_tests
from app.ops.resources import (
    OllamaMetrics,
    PostgresMetrics,
    RedisMetrics,
    ResourceSnapshot,
    SystemMetrics,
)


NOW = datetime(2026, 8, 18, 11, 0, tzinfo=UTC)


def snapshot() -> ResourceSnapshot:
    return ResourceSnapshot(
        collected_at=NOW,
        system=SystemMetrics(
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
        postgres=PostgresMetrics(
            connections=7,
            database_size_bytes=123_456,
            scheduler_lag_seconds=42.0,
            ingest_lag_seconds=180.0,
        ),
        redis=RedisMetrics(
            memory_used_bytes=9_000,
            keys=25,
            execution_queue_depth=3,
            execution_queue_lag_seconds=12.0,
        ),
        ollama=OllamaMetrics(
            reachable=True,
            loaded_models=1,
            configured_model_loaded=True,
        ),
        probe_errors=(),
    )


def test_prometheus_render_contains_minimum_resource_and_operational_metrics():
    text = render_metrics(snapshot_provider=snapshot).decode()

    required = {
        "signalai_memory_used_bytes 6000.0",
        "signalai_memory_limit_bytes 8000.0",
        "signalai_swap_used_bytes 500.0",
        "signalai_cpu_usage_seconds_total 321.5",
        "signalai_system_load1 1.2",
        "signalai_disk_used_bytes 40000.0",
        "signalai_inode_used 120.0",
        "signalai_postgres_connections 7.0",
        "signalai_postgres_database_size_bytes 123456.0",
        "signalai_redis_memory_used_bytes 9000.0",
        "signalai_execution_queue_depth 3.0",
        "signalai_execution_queue_lag_seconds 12.0",
        "signalai_scheduler_lag_seconds 42.0",
        "signalai_ingest_lag_seconds 180.0",
        "signalai_ollama_loaded_models 1.0",
        "signalai_ollama_configured_model_loaded 1.0",
        "signalai_container_oom_events_total 2.0",
        "signalai_container_oom_kills_total 1.0",
    }
    assert required <= set(text.splitlines())
    assert "signalai_websocket_disconnects_total" in text


def test_metrics_endpoint_fails_closed_and_health_remains_public(monkeypatch):
    reset_http_metrics_for_tests()
    monkeypatch.delenv("SIGNALAI_METRICS_TOKEN", raising=False)
    client = TestClient(app)

    response = client.get("/metrics")
    assert response.status_code == 503

    health = client.get("/health")
    assert health.status_code == 200


def test_metrics_endpoint_requires_dedicated_bearer_token(monkeypatch):
    reset_http_metrics_for_tests()
    monkeypatch.setenv("SIGNALAI_METRICS_TOKEN", "metrics-secret")
    client = TestClient(app)

    denied = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    assert denied.status_code == 401

    allowed = client.get(
        "/metrics", headers={"Authorization": "Bearer metrics-secret"}
    )
    assert allowed.status_code == 200
    assert allowed.headers["content-type"].startswith("text/plain")
    assert "signalai_http_request_duration_seconds" in allowed.text
    assert "signalai_http_requests_total" in allowed.text
