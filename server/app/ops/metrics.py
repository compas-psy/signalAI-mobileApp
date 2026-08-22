"""Owner-only Prometheus exposition and low-overhead request metrics.

The exposition format is intentionally implemented in a tiny local module
instead of adding a runtime dependency for a single trusted endpoint. Metric
state is process-local; resource probes read authoritative system/dependency
state at scrape time and fail open when an optional dependency is unavailable.
"""

from __future__ import annotations

import hmac
import os
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import Request
from fastapi.responses import Response

from .resources import ResourceSnapshot, collect_resource_snapshot


_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


@dataclass
class _HttpState:
    requests: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))
    errors: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))
    duration_count: int = 0
    duration_sum: float = 0.0
    duration_buckets: list[int] = field(default_factory=lambda: [0] * len(_BUCKETS))
    websocket_disconnects: int = 0
    disk_baseline_used_bytes: int | None = None


_STATE = _HttpState()
_LOCK = threading.Lock()


def _status_class(status: int) -> str:
    return f"{status // 100}xx"


def _observe_http(method: str, status: int, elapsed: float) -> None:
    group = _status_class(status)
    with _LOCK:
        _STATE.requests[(method, group)] += 1
        if status >= 500:
            _STATE.errors[(method, group)] += 1
        _STATE.duration_count += 1
        _STATE.duration_sum += max(0.0, elapsed)
        for index, boundary in enumerate(_BUCKETS):
            if elapsed <= boundary:
                _STATE.duration_buckets[index] += 1


def _observe_websocket_disconnect() -> None:
    with _LOCK:
        _STATE.websocket_disconnects += 1


def reset_http_metrics_for_tests() -> None:
    global _STATE
    with _LOCK:
        _STATE = _HttpState()


class ObservabilityMiddleware:
    """ASGI pass-through instrumentation; never changes application decisions."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        scope_type = scope.get("type")
        if scope_type == "websocket":
            async def observed_receive():
                message = await receive()
                if message.get("type") == "websocket.disconnect":
                    _observe_websocket_disconnect()
                return message

            await self.app(scope, observed_receive, send)
            return
        if scope_type != "http" or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = 500

        async def observed_send(message):
            nonlocal status
            if message.get("type") == "http.response.start":
                status = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        finally:
            _observe_http(
                str(scope.get("method", "UNKNOWN")),
                status,
                time.perf_counter() - started,
            )


def _sample(name: str, value: int | float, *, metric_type: str = "gauge") -> list[str]:
    return [f"# TYPE {name} {metric_type}", f"{name} {float(value)}"]


def _disk_growth_bytes(current_used: int) -> int:
    with _LOCK:
        baseline = _STATE.disk_baseline_used_bytes
        if baseline is None:
            _STATE.disk_baseline_used_bytes = current_used
            return 0
        return current_used - baseline


def _resource_lines(snapshot: ResourceSnapshot) -> list[str]:
    s = snapshot.system
    p = snapshot.postgres
    r = snapshot.redis
    o = snapshot.ollama
    lines: list[str] = []
    for name, value, metric_type in (
        ("signalai_memory_used_bytes", s.memory_used_bytes, "gauge"),
        ("signalai_memory_limit_bytes", s.memory_limit_bytes, "gauge"),
        ("signalai_swap_used_bytes", s.swap_used_bytes, "gauge"),
        ("signalai_cpu_usage_seconds_total", s.cpu_usage_seconds, "counter"),
        ("signalai_system_load1", s.load1, "gauge"),
        ("signalai_system_load5", s.load5, "gauge"),
        ("signalai_system_load15", s.load15, "gauge"),
        ("signalai_disk_used_bytes", s.disk_used_bytes, "gauge"),
        ("signalai_disk_total_bytes", s.disk_total_bytes, "gauge"),
        (
            "signalai_disk_growth_bytes_since_process_start",
            _disk_growth_bytes(s.disk_used_bytes),
            "gauge",
        ),
        ("signalai_inode_used", s.inode_used, "gauge"),
        ("signalai_inode_total", s.inode_total, "gauge"),
        ("signalai_postgres_connections", p.connections, "gauge"),
        ("signalai_postgres_database_size_bytes", p.database_size_bytes, "gauge"),
        ("signalai_redis_memory_used_bytes", r.memory_used_bytes, "gauge"),
        ("signalai_redis_keys", r.keys, "gauge"),
        ("signalai_execution_queue_depth", r.execution_queue_depth, "gauge"),
        ("signalai_execution_queue_lag_seconds", r.execution_queue_lag_seconds, "gauge"),
        ("signalai_scheduler_lag_seconds", p.scheduler_lag_seconds, "gauge"),
        ("signalai_ingest_lag_seconds", p.ingest_lag_seconds, "gauge"),
        ("signalai_ollama_reachable", int(o.reachable), "gauge"),
        ("signalai_ollama_loaded_models", o.loaded_models, "gauge"),
        (
            "signalai_ollama_configured_model_loaded",
            int(o.configured_model_loaded),
            "gauge",
        ),
        ("signalai_container_oom_events_total", s.cgroup_oom_events, "counter"),
        ("signalai_container_oom_kills_total", s.cgroup_oom_kills, "counter"),
    ):
        lines.extend(_sample(name, value, metric_type=metric_type))

    failed = {item.split(":", 1)[0] for item in snapshot.probe_errors}
    for probe in ("system", "postgres", "redis", "ollama"):
        lines.append(
            f'signalai_resource_probe_up{{probe="{probe}"}} '
            f'{0.0 if probe in failed else 1.0}'
        )
    lines.append(f"signalai_resource_probe_errors {float(len(snapshot.probe_errors))}")
    return lines


def _http_lines() -> list[str]:
    with _LOCK:
        requests = dict(_STATE.requests)
        errors = dict(_STATE.errors)
        count = _STATE.duration_count
        total = _STATE.duration_sum
        buckets = list(_STATE.duration_buckets)
        websocket_disconnects = _STATE.websocket_disconnects

    lines = ["# TYPE signalai_http_requests_total counter"]
    if requests:
        for (method, group), value in sorted(requests.items()):
            lines.append(
                f'signalai_http_requests_total{{method="{method}",status_class="{group}"}} '
                f'{float(value)}'
            )
    else:
        lines.append(
            'signalai_http_requests_total{method="NONE",status_class="none"} 0.0'
        )

    lines.append("# TYPE signalai_http_errors_total counter")
    if errors:
        for (method, group), value in sorted(errors.items()):
            lines.append(
                f'signalai_http_errors_total{{method="{method}",status_class="{group}"}} '
                f'{float(value)}'
            )
    else:
        lines.append(
            'signalai_http_errors_total{method="NONE",status_class="none"} 0.0'
        )

    lines.append("# TYPE signalai_http_request_duration_seconds histogram")
    for boundary, value in zip(_BUCKETS, buckets, strict=True):
        lines.append(
            f'signalai_http_request_duration_seconds_bucket{{le="{boundary}"}} '
            f'{float(value)}'
        )
    lines.append(
        f'signalai_http_request_duration_seconds_bucket{{le="+Inf"}} {float(count)}'
    )
    lines.append(f"signalai_http_request_duration_seconds_sum {total}")
    lines.append(f"signalai_http_request_duration_seconds_count {float(count)}")
    lines.extend(
        _sample(
            "signalai_websocket_disconnects_total",
            websocket_disconnects,
            metric_type="counter",
        )
    )
    return lines


def render_metrics(
    *,
    snapshot_provider: Callable[[], ResourceSnapshot] = collect_resource_snapshot,
) -> bytes:
    snapshot = snapshot_provider()
    lines = [
        "# SignalAI owner-only operational telemetry",
        *_resource_lines(snapshot),
        *_http_lines(),
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def metrics_response(request: Request) -> Response:
    expected = os.environ.get("SIGNALAI_METRICS_TOKEN", "").strip()
    if not expected:
        return Response(
            status_code=503,
            content="owner metrics token is not configured",
        )
    raw = request.headers.get("authorization", "")
    scheme, _, token = raw.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip(), expected):
        return Response(
            status_code=401,
            content="unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Response(
        content=render_metrics(),
        status_code=200,
        headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
    )


__all__ = [
    "ObservabilityMiddleware",
    "metrics_response",
    "render_metrics",
    "reset_http_metrics_for_tests",
]
