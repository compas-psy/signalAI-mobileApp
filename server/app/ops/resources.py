"""Read-only resource probes for the owner-only metrics endpoint.

Every probe is best effort. Observability is never allowed to become a trading
availability dependency: a failed Redis/Ollama/Postgres probe produces a
partial snapshot and an explicit probe error instead of raising into request
or scheduler paths.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

from redis import Redis
from sqlalchemy import text

from ..db import get_engine


@dataclass(frozen=True, slots=True)
class SystemMetrics:
    memory_used_bytes: int
    memory_limit_bytes: int
    swap_used_bytes: int
    cpu_usage_seconds: float
    load1: float
    load5: float
    load15: float
    disk_used_bytes: int
    disk_total_bytes: int
    inode_used: int
    inode_total: int
    cgroup_oom_events: int
    cgroup_oom_kills: int

    @classmethod
    def zero(cls) -> "SystemMetrics":
        return cls(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class PostgresMetrics:
    connections: int
    database_size_bytes: int
    scheduler_lag_seconds: float
    ingest_lag_seconds: float

    @classmethod
    def zero(cls) -> "PostgresMetrics":
        return cls(0, 0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class RedisMetrics:
    memory_used_bytes: int
    keys: int
    execution_queue_depth: int
    execution_queue_lag_seconds: float

    @classmethod
    def zero(cls) -> "RedisMetrics":
        return cls(0, 0, 0, 0.0)


@dataclass(frozen=True, slots=True)
class OllamaMetrics:
    reachable: bool
    loaded_models: int
    configured_model_loaded: bool

    @classmethod
    def unavailable(cls) -> "OllamaMetrics":
        return cls(False, 0, False)


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    collected_at: datetime
    system: SystemMetrics
    postgres: PostgresMetrics
    redis: RedisMetrics
    ollama: OllamaMetrics
    probe_errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResourceProbes:
    system: Callable[[], SystemMetrics]
    postgres: Callable[[datetime], PostgresMetrics]
    redis: Callable[[datetime], RedisMetrics]
    ollama: Callable[[], OllamaMetrics]


def _read_int(path: Path, *, unlimited: int = 0) -> int:
    raw = path.read_text(encoding="utf-8").strip()
    if raw == "max":
        return unlimited
    return int(raw)


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, raw = line.split(":", 1)
        parts = raw.strip().split()
        if not parts:
            continue
        value = int(parts[0])
        values[key] = value * 1024 if len(parts) > 1 and parts[1] == "kB" else value
    return values


def _cpu_usage_seconds() -> float:
    cgroup = Path("/sys/fs/cgroup/cpu.stat")
    if cgroup.exists():
        for line in cgroup.read_text(encoding="utf-8").splitlines():
            key, value = line.split(maxsplit=1)
            if key == "usage_usec":
                return int(value) / 1_000_000
    first = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0]
    ticks = sum(int(value) for value in first.split()[1:])
    return ticks / os.sysconf("SC_CLK_TCK")


def _memory_events() -> tuple[int, int]:
    path = Path("/sys/fs/cgroup/memory.events")
    if not path.exists():
        return 0, 0
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = line.split(maxsplit=1)
        values[key] = int(value)
    return values.get("oom", 0), values.get("oom_kill", 0)


def system_probe() -> SystemMetrics:
    mem = _meminfo()
    host_total = mem.get("MemTotal", 0)
    host_used = max(0, host_total - mem.get("MemAvailable", 0))
    swap_used = max(0, mem.get("SwapTotal", 0) - mem.get("SwapFree", 0))

    current_path = Path("/sys/fs/cgroup/memory.current")
    limit_path = Path("/sys/fs/cgroup/memory.max")
    memory_used = _read_int(current_path) if current_path.exists() else host_used
    memory_limit = _read_int(limit_path) if limit_path.exists() else host_total
    if memory_limit <= 0:
        memory_limit = host_total

    load = Path("/proc/loadavg").read_text(encoding="utf-8").split()[:3]
    stat = os.statvfs("/")
    disk_total = stat.f_blocks * stat.f_frsize
    disk_free = stat.f_bavail * stat.f_frsize
    inode_total = stat.f_files
    inode_free = stat.f_favail
    oom, oom_kill = _memory_events()

    return SystemMetrics(
        memory_used_bytes=memory_used,
        memory_limit_bytes=memory_limit,
        swap_used_bytes=swap_used,
        cpu_usage_seconds=_cpu_usage_seconds(),
        load1=float(load[0]),
        load5=float(load[1]),
        load15=float(load[2]),
        disk_used_bytes=max(0, disk_total - disk_free),
        disk_total_bytes=disk_total,
        inode_used=max(0, inode_total - inode_free),
        inode_total=inode_total,
        cgroup_oom_events=oom,
        cgroup_oom_kills=oom_kill,
    )


def _lag_seconds(now: datetime, value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return max(0.0, (now - value).total_seconds())


def postgres_probe(now: datetime) -> PostgresMetrics:
    with get_engine().connect() as connection:
        connections = connection.execute(
            text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
        ).scalar_one()
        size = connection.execute(
            text("SELECT pg_database_size(current_database())")
        ).scalar_one()
        scheduler_at = connection.execute(
            text(
                "SELECT max(occurred_at) FROM data_quality_events "
                "WHERE source = 'scheduler'"
            )
        ).scalar_one_or_none()
        newest_bar = connection.execute(
            text(
                "SELECT max(open_time) FROM bars "
                "WHERE is_closed IS TRUE AND timeframe = '1h'"
            )
        ).scalar_one_or_none()
    return PostgresMetrics(
        connections=int(connections),
        database_size_bytes=int(size),
        scheduler_lag_seconds=_lag_seconds(now, scheduler_at),
        ingest_lag_seconds=_lag_seconds(now, newest_bar),
    )


def _queued_at(raw: object) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(str(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("enqueued_at", "queued_at", "created_at"):
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return None


def redis_probe(now: datetime) -> RedisMetrics:
    url = os.environ.get("SIGNALAI_REDIS_URL", "redis://127.0.0.1:6379/0")
    queue_key = os.environ.get("SIGNALAI_EXECUTION_QUEUE_KEY", "signalai:execution:queue")
    client = Redis.from_url(
        url,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
        decode_responses=False,
    )
    try:
        info = client.info(section="memory")
        keys = int(client.dbsize())
        depth = int(client.llen(queue_key))
        oldest = _queued_at(client.lindex(queue_key, 0)) if depth else None
    finally:
        client.close()
    return RedisMetrics(
        memory_used_bytes=int(info.get("used_memory", 0)),
        keys=keys,
        execution_queue_depth=depth,
        execution_queue_lag_seconds=_lag_seconds(now, oldest),
    )


def _ollama_ps_url(base: str) -> str:
    cleaned = base.rstrip("/")
    if cleaned.endswith("/v1"):
        cleaned = cleaned[:-3]
    return cleaned + "/api/ps"


def ollama_probe() -> OllamaMetrics:
    base = os.environ.get("SIGNALAI_LLM_BASE_URL", "").strip()
    if not base:
        return OllamaMetrics.unavailable()
    request = Request(_ollama_ps_url(base), headers={"Accept": "application/json"})
    with urlopen(request, timeout=2.0) as response:  # noqa: S310 - trusted configured URL
        payload = json.loads(response.read().decode("utf-8"))
    models = payload.get("models", []) if isinstance(payload, dict) else []
    names = {
        str(item.get("name") or item.get("model") or "")
        for item in models
        if isinstance(item, dict)
    }
    configured = os.environ.get("SIGNALAI_LLM_MODEL", "qwen3.5:4b").strip()
    return OllamaMetrics(
        reachable=True,
        loaded_models=len(models),
        configured_model_loaded=configured in names,
    )


DEFAULT_PROBES = ResourceProbes(
    system=system_probe,
    postgres=postgres_probe,
    redis=redis_probe,
    ollama=ollama_probe,
)


def collect_resource_snapshot(
    *,
    now: datetime | None = None,
    probes: ResourceProbes = DEFAULT_PROBES,
) -> ResourceSnapshot:
    moment = now or datetime.now(UTC)
    errors: list[str] = []

    try:
        system = probes.system()
    except Exception as exc:  # observability must fail open
        errors.append(f"system:{type(exc).__name__}")
        system = SystemMetrics.zero()
    try:
        postgres = probes.postgres(moment)
    except Exception as exc:
        errors.append(f"postgres:{type(exc).__name__}")
        postgres = PostgresMetrics.zero()
    try:
        redis = probes.redis(moment)
    except Exception as exc:
        errors.append(f"redis:{type(exc).__name__}")
        redis = RedisMetrics.zero()
    try:
        ollama = probes.ollama()
    except Exception as exc:
        errors.append(f"ollama:{type(exc).__name__}")
        ollama = OllamaMetrics.unavailable()

    return ResourceSnapshot(
        collected_at=moment,
        system=system,
        postgres=postgres,
        redis=redis,
        ollama=ollama,
        probe_errors=tuple(errors),
    )


__all__ = [
    "OllamaMetrics",
    "PostgresMetrics",
    "RedisMetrics",
    "ResourceProbes",
    "ResourceSnapshot",
    "SystemMetrics",
    "collect_resource_snapshot",
]
