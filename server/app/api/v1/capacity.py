"""Read-only server capacity status for the owner UI.

The endpoint combines a fresh fail-open SAI-015 resource snapshot with the
latest persisted SAI-021 remediation event. It never runs the pressure
classifier, backpressure policy, retention cleanup or Ollama shedding.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import AuditEvent
from ...ops.resources import ResourceSnapshot, collect_resource_snapshot


router = APIRouter(prefix="/capacity", tags=["capacity"])
_ACTION = "RESOURCE_REMEDIATION"
_SUBJECT = "resource-capacity"


def _ratio(used: int, capacity: int) -> float | None:
    if used < 0 or capacity <= 0:
        return None
    return used / capacity


def _number(value: int | float | Decimal) -> int | float:
    return int(value) if isinstance(value, int) else float(value)


def _latest_remediation(db: Session) -> dict | None:
    audit = db.execute(
        select(AuditEvent)
        .where(
            AuditEvent.action == _ACTION,
            AuditEvent.subject == _SUBJECT,
        )
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if audit is None:
        return None

    payload = audit.after_json if isinstance(audit.after_json, dict) else {}
    ollama = payload.get("ollama") if isinstance(payload.get("ollama"), dict) else {}
    retention = (
        payload.get("retention")
        if isinstance(payload.get("retention"), dict)
        else {}
    )
    reasons = payload.get("pressure_reasons")
    if not isinstance(reasons, list):
        reasons = []

    return {
        "audit_id": str(audit.id),
        "occurred_at": audit.occurred_at.isoformat(),
        "pressure_state": payload.get("pressure_state"),
        "effective_state": payload.get("effective_state"),
        "new_entries": payload.get("new_entries"),
        "reasons": reasons,
        "ollama_status": ollama.get("status"),
        "retention_status": retention.get("status"),
        "retention_deleted_files": int(retention.get("deleted_files") or 0),
        "retention_deleted_bytes": int(retention.get("deleted_bytes") or 0),
        "fingerprint": payload.get("fingerprint"),
    }


def _snapshot_payload(snapshot: ResourceSnapshot) -> dict:
    system = snapshot.system
    return {
        "collected_at": snapshot.collected_at.isoformat(),
        "system": {
            "memory": {
                "used_bytes": system.memory_used_bytes,
                "limit_bytes": system.memory_limit_bytes,
                "used_ratio": _ratio(
                    system.memory_used_bytes,
                    system.memory_limit_bytes,
                ),
            },
            "swap_used_bytes": system.swap_used_bytes,
            "load1": system.load1,
            "load5": system.load5,
            "load15": system.load15,
            "disk": {
                "used_bytes": system.disk_used_bytes,
                "total_bytes": system.disk_total_bytes,
                "used_ratio": _ratio(
                    system.disk_used_bytes,
                    system.disk_total_bytes,
                ),
            },
            "inodes": {
                "used": system.inode_used,
                "total": system.inode_total,
                "used_ratio": _ratio(system.inode_used, system.inode_total),
            },
            "oom_events": system.cgroup_oom_events,
            "oom_kills": system.cgroup_oom_kills,
        },
        "postgres": {
            "connections": snapshot.postgres.connections,
            "database_size_bytes": snapshot.postgres.database_size_bytes,
            "scheduler_lag_seconds": snapshot.postgres.scheduler_lag_seconds,
            "ingest_lag_seconds": snapshot.postgres.ingest_lag_seconds,
        },
        "redis": {
            "memory_used_bytes": snapshot.redis.memory_used_bytes,
            "keys": snapshot.redis.keys,
            "execution_queue_depth": snapshot.redis.execution_queue_depth,
            "execution_queue_lag_seconds": snapshot.redis.execution_queue_lag_seconds,
        },
        "ollama": {
            "reachable": snapshot.ollama.reachable,
            "loaded_models": snapshot.ollama.loaded_models,
            "configured_model_loaded": snapshot.ollama.configured_model_loaded,
        },
        "probe_errors": list(snapshot.probe_errors),
    }


@router.get("")
def capacity_status(db: Session = Depends(get_db)) -> dict:
    snapshot = collect_resource_snapshot()
    payload = _snapshot_payload(snapshot)
    payload["latest_remediation"] = _latest_remediation(db)
    return payload


__all__ = ["router"]
