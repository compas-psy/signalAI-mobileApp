from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.v1 import capacity as capacity_routes
from app.db import get_db
from app.main import app
from app.models import AuditEvent, NotificationOutbox
from app.ops.resources import (
    OllamaMetrics,
    PostgresMetrics,
    RedisMetrics,
    ResourceSnapshot,
    SystemMetrics,
)
from tests.conftest import DEVICE_HEADERS


NOW = datetime(2026, 8, 18, 18, 30, tzinfo=UTC)
GB = 1024**3


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as c:
        yield c
    app.dependency_overrides.clear()


def _snapshot(*, errors: tuple[str, ...] = ()) -> ResourceSnapshot:
    return ResourceSnapshot(
        collected_at=NOW,
        system=SystemMetrics(
            memory_used_bytes=6 * GB,
            memory_limit_bytes=8 * GB,
            swap_used_bytes=256 * 1024**2,
            cpu_usage_seconds=1234.5,
            load1=1.25,
            load5=0.75,
            load15=0.50,
            disk_used_bytes=62 * GB,
            disk_total_bytes=100 * GB,
            inode_used=300_000,
            inode_total=1_000_000,
            cgroup_oom_events=2,
            cgroup_oom_kills=0,
        ),
        postgres=PostgresMetrics(
            connections=7,
            database_size_bytes=2 * GB,
            scheduler_lag_seconds=12.0,
            ingest_lag_seconds=90.0,
        ),
        redis=RedisMetrics(
            memory_used_bytes=64 * 1024**2,
            keys=42,
            execution_queue_depth=3,
            execution_queue_lag_seconds=45.0,
        ),
        ollama=OllamaMetrics(
            reachable=True,
            loaded_models=1,
            configured_model_loaded=True,
        ),
        probe_errors=errors,
    )


def _seed_remediation(session) -> AuditEvent:
    audit = AuditEvent(
        occurred_at=NOW - timedelta(minutes=5),
        actor="resource-autopilot",
        action="RESOURCE_REMEDIATION",
        subject="resource-capacity",
        detail="PRESSURE; ollama=UNLOADED; retention=CLEANED",
        before_json={},
        after_json={
            "pressure_state": "PRESSURE",
            "effective_state": "PRESSURE",
            "new_entries": "ALLOW",
            "pressure_reasons": ["disk_headroom_pressure"],
            "ollama": {"status": "UNLOADED"},
            "retention": {
                "status": "CLEANED",
                "deleted_files": 4,
                "deleted_bytes": 4096,
            },
            "fingerprint": "f" * 64,
        },
        trace_id="resource-ffffffffffffffff",
    )
    session.add(audit)
    session.flush()
    return audit


def _counts(session) -> tuple[int, int]:
    audit_count = int(
        session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()
    )
    outbox_count = int(
        session.execute(select(func.count()).select_from(NotificationOutbox)).scalar_one()
    )
    return audit_count, outbox_count


def test_capacity_endpoint_exposes_live_snapshot_and_latest_persisted_remediation(
    client,
    session,
    monkeypatch,
):
    audit = _seed_remediation(session)
    monkeypatch.setattr(capacity_routes, "collect_resource_snapshot", _snapshot)
    before = _counts(session)

    response = client.get("/api/v1/capacity")

    assert response.status_code == 200
    body = response.json()
    assert body["collected_at"] == NOW.isoformat()
    assert body["system"]["memory"] == {
        "used_bytes": 6 * GB,
        "limit_bytes": 8 * GB,
        "used_ratio": pytest.approx(0.75),
    }
    assert body["system"]["disk"]["used_ratio"] == pytest.approx(0.62)
    assert body["system"]["inodes"]["used_ratio"] == pytest.approx(0.30)
    assert body["system"]["oom_kills"] == 0
    assert body["postgres"]["connections"] == 7
    assert body["postgres"]["scheduler_lag_seconds"] == pytest.approx(12.0)
    assert body["redis"]["execution_queue_depth"] == 3
    assert body["redis"]["execution_queue_lag_seconds"] == pytest.approx(45.0)
    assert body["ollama"] == {
        "reachable": True,
        "loaded_models": 1,
        "configured_model_loaded": True,
    }
    assert body["probe_errors"] == []
    assert body["latest_remediation"] == {
        "audit_id": str(audit.id),
        "occurred_at": (NOW - timedelta(minutes=5)).isoformat(),
        "pressure_state": "PRESSURE",
        "effective_state": "PRESSURE",
        "new_entries": "ALLOW",
        "reasons": ["disk_headroom_pressure"],
        "ollama_status": "UNLOADED",
        "retention_status": "CLEANED",
        "retention_deleted_files": 4,
        "retention_deleted_bytes": 4096,
        "fingerprint": "f" * 64,
    }
    assert _counts(session) == before


def test_capacity_endpoint_has_explicit_unobserved_state_without_remediation(
    client,
    monkeypatch,
):
    monkeypatch.setattr(capacity_routes, "collect_resource_snapshot", _snapshot)

    response = client.get("/api/v1/capacity")

    assert response.status_code == 200
    body = response.json()
    assert body["latest_remediation"] is None
    assert body["probe_errors"] == []


def test_capacity_endpoint_preserves_probe_errors_and_unknown_ratios(
    client,
    monkeypatch,
):
    sample = _snapshot(errors=("redis:ConnectionError", "ollama:TimeoutError"))
    broken = ResourceSnapshot(
        collected_at=sample.collected_at,
        system=SystemMetrics(
            memory_used_bytes=0,
            memory_limit_bytes=0,
            swap_used_bytes=0,
            cpu_usage_seconds=sample.system.cpu_usage_seconds,
            load1=sample.system.load1,
            load5=sample.system.load5,
            load15=sample.system.load15,
            disk_used_bytes=0,
            disk_total_bytes=0,
            inode_used=0,
            inode_total=0,
            cgroup_oom_events=0,
            cgroup_oom_kills=0,
        ),
        postgres=sample.postgres,
        redis=RedisMetrics.zero(),
        ollama=OllamaMetrics.unavailable(),
        probe_errors=sample.probe_errors,
    )
    monkeypatch.setattr(capacity_routes, "collect_resource_snapshot", lambda: broken)

    response = client.get("/api/v1/capacity")

    assert response.status_code == 200
    body = response.json()
    assert body["system"]["memory"]["used_ratio"] is None
    assert body["system"]["disk"]["used_ratio"] is None
    assert body["system"]["inodes"]["used_ratio"] is None
    assert body["ollama"]["reachable"] is False
    assert body["probe_errors"] == ["redis:ConnectionError", "ollama:TimeoutError"]


def test_capacity_api_is_get_only(client):
    response = client.post("/api/v1/capacity", json={"action": "cleanup"})
    assert response.status_code == 405
