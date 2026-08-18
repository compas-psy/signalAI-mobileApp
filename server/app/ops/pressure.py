"""Pure resource-pressure classification for the Server Autopilot.

This module observes ``ResourceSnapshot`` values and produces a deterministic
state only. It deliberately has no side effects: it does not throttle work,
stop Ollama, change execution mode, halt entries, or touch trading state.
Those actions belong to later Resource Autopilot slices.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from .resources import ResourceSnapshot


class PressureState(str, Enum):
    NORMAL = "NORMAL"
    WATCH = "WATCH"
    PRESSURE = "PRESSURE"
    CRITICAL = "CRITICAL"
    RECOVERING = "RECOVERING"


@dataclass(frozen=True, slots=True)
class PressureContext:
    execution_mode: str = "PAPER"
    open_positions: int = 0

    def __post_init__(self) -> None:
        if self.open_positions < 0:
            raise ValueError("open_positions must be non-negative")


@dataclass(frozen=True, slots=True)
class PressurePolicy:
    """Initial operational policy; these are not trading hard limits."""

    memory_watch_ratio: float = 0.80
    memory_pressure_ratio: float = 0.87
    memory_critical_ratio: float = 0.95
    disk_watch_ratio: float = 0.82
    disk_pressure_ratio: float = 0.90
    disk_critical_ratio: float = 0.97
    inode_watch_ratio: float = 0.85
    inode_pressure_ratio: float = 0.93
    inode_critical_ratio: float = 0.98
    swap_watch_bytes: int = 256 * 1024 * 1024
    scheduler_lag_watch_seconds: float = 60.0
    scheduler_lag_pressure_seconds: float = 120.0
    queue_lag_watch_seconds: float = 30.0
    queue_lag_pressure_seconds: float = 120.0
    queue_depth_watch: int = 1
    queue_depth_pressure: int = 8
    # There is no portable safe default for Postgres saturation: deployment
    # max_connections/pool budgets vary. Keep this signal disabled unless both
    # explicit operational thresholds are configured by the caller.
    postgres_connections_watch: int | None = None
    postgres_connections_pressure: int | None = None
    memory_trend_delta: float = 0.03
    disk_trend_delta: float = 0.03
    inode_trend_delta: float = 0.03
    scheduler_trend_delta_seconds: float = 60.0
    queue_trend_delta_seconds: float = 30.0
    pressure_score: int = 4
    recovery_window: timedelta = timedelta(minutes=15)

    def __post_init__(self) -> None:
        for label, values in (
            (
                "memory",
                (
                    self.memory_watch_ratio,
                    self.memory_pressure_ratio,
                    self.memory_critical_ratio,
                ),
            ),
            (
                "disk",
                (
                    self.disk_watch_ratio,
                    self.disk_pressure_ratio,
                    self.disk_critical_ratio,
                ),
            ),
            (
                "inode",
                (
                    self.inode_watch_ratio,
                    self.inode_pressure_ratio,
                    self.inode_critical_ratio,
                ),
            ),
        ):
            watch, pressure, critical = values
            if not 0 < watch < pressure < critical <= 1:
                raise ValueError(
                    f"{label} thresholds must satisfy 0 < watch < pressure < critical <= 1"
                )
        if self.swap_watch_bytes < 0:
            raise ValueError("swap_watch_bytes must be non-negative")
        if self.scheduler_lag_watch_seconds < 0 or self.queue_lag_watch_seconds < 0:
            raise ValueError("lag watch thresholds must be non-negative")
        if self.scheduler_lag_pressure_seconds < self.scheduler_lag_watch_seconds:
            raise ValueError("scheduler pressure lag must be >= watch lag")
        if self.queue_lag_pressure_seconds < self.queue_lag_watch_seconds:
            raise ValueError("queue pressure lag must be >= watch lag")
        if self.queue_depth_watch < 0 or self.queue_depth_pressure < self.queue_depth_watch:
            raise ValueError("queue depth thresholds are invalid")
        pg_watch = self.postgres_connections_watch
        pg_pressure = self.postgres_connections_pressure
        if (pg_watch is None) != (pg_pressure is None):
            raise ValueError(
                "postgres connection thresholds must be both set or both disabled"
            )
        if pg_watch is not None and pg_pressure is not None:
            if pg_watch <= 0 or pg_pressure <= pg_watch:
                raise ValueError(
                    "postgres connection thresholds must satisfy 0 < watch < pressure"
                )
        if self.pressure_score <= 0:
            raise ValueError("pressure_score must be positive")
        if self.recovery_window <= timedelta(0):
            raise ValueError("recovery_window must be positive")


@dataclass(frozen=True, slots=True)
class PressureAssessment:
    state: PressureState
    score: int
    reasons: tuple[str, ...]
    active_dimensions: int


@dataclass(frozen=True, slots=True)
class _RawAssessment:
    score: int
    reasons: tuple[str, ...]
    dimensions: frozenset[str]
    has_critical_headroom: bool
    oom_kill_detected: bool


class PressureClassifier:
    """Stateful only for trend and recovery hysteresis; never performs actions."""

    _ACTIVE_EXECUTION_MODES = frozenset({"CANARY_LIVE", "LIVE"})

    def __init__(self, *, policy: PressurePolicy | None = None) -> None:
        self.policy = policy or PressurePolicy()
        self._previous: ResourceSnapshot | None = None
        self._state = PressureState.NORMAL
        self._recovery_started_at = None

    def evaluate(
        self,
        snapshot: ResourceSnapshot,
        *,
        context: PressureContext | None = None,
    ) -> PressureAssessment:
        context = context or PressureContext()
        raw = self._assess(snapshot, context)
        candidate = self._state_from(raw)
        state = self._apply_hysteresis(snapshot, candidate)
        self._previous = snapshot
        self._state = state
        return PressureAssessment(
            state=state,
            score=raw.score,
            reasons=raw.reasons,
            active_dimensions=len(raw.dimensions),
        )

    def _assess(
        self,
        snapshot: ResourceSnapshot,
        context: PressureContext,
    ) -> _RawAssessment:
        score = 0
        reasons: list[str] = []
        dimensions: set[str] = set()
        critical_headroom = False

        def add(reason: str, weight: int, dimension: str) -> None:
            nonlocal score
            if reason in reasons:
                return
            reasons.append(reason)
            score += weight
            dimensions.add(dimension)

        system = snapshot.system
        memory = _ratio(system.memory_used_bytes, system.memory_limit_bytes)
        disk = _ratio(system.disk_used_bytes, system.disk_total_bytes)
        inode = _ratio(system.inode_used, system.inode_total)

        memory_weight, memory_reason, memory_critical = _headroom_signal(
            memory,
            watch=self.policy.memory_watch_ratio,
            pressure=self.policy.memory_pressure_ratio,
            critical=self.policy.memory_critical_ratio,
            name="memory",
        )
        if memory_reason:
            add(memory_reason, memory_weight, "headroom")
        critical_headroom |= memory_critical

        disk_weight, disk_reason, disk_critical = _headroom_signal(
            disk,
            watch=self.policy.disk_watch_ratio,
            pressure=self.policy.disk_pressure_ratio,
            critical=self.policy.disk_critical_ratio,
            name="disk",
        )
        if disk_reason:
            add(disk_reason, disk_weight, "headroom")
        critical_headroom |= disk_critical

        inode_weight, inode_reason, inode_critical = _headroom_signal(
            inode,
            watch=self.policy.inode_watch_ratio,
            pressure=self.policy.inode_pressure_ratio,
            critical=self.policy.inode_critical_ratio,
            name="inode",
        )
        if inode_reason:
            add(inode_reason, inode_weight, "headroom")
        critical_headroom |= inode_critical

        if (
            self.policy.swap_watch_bytes > 0
            and system.swap_used_bytes >= self.policy.swap_watch_bytes
        ):
            add("swap_in_use", 1, "headroom")

        pg_watch = self.policy.postgres_connections_watch
        pg_pressure = self.policy.postgres_connections_pressure
        if pg_watch is not None and pg_pressure is not None:
            connections = max(0, snapshot.postgres.connections)
            if connections >= pg_pressure:
                add("postgres_connections_pressure", 2, "database_headroom")
            elif connections >= pg_watch:
                add("postgres_connections_watch", 1, "database_headroom")

        scheduler_lag = max(0.0, snapshot.postgres.scheduler_lag_seconds)
        if scheduler_lag >= self.policy.scheduler_lag_pressure_seconds:
            add("scheduler_lag", 2, "workload")
        elif scheduler_lag >= self.policy.scheduler_lag_watch_seconds:
            add("scheduler_lag", 1, "workload")

        queue_lag = max(0.0, snapshot.redis.execution_queue_lag_seconds)
        queue_depth = max(0, snapshot.redis.execution_queue_depth)
        if queue_lag >= self.policy.queue_lag_pressure_seconds:
            add("execution_queue_lag", 2, "workload")
        elif queue_lag >= self.policy.queue_lag_watch_seconds:
            add("execution_queue_lag", 1, "workload")
        if queue_depth >= self.policy.queue_depth_pressure:
            add("execution_queue_depth", 2, "workload")
        elif queue_depth >= self.policy.queue_depth_watch:
            add("execution_queue_depth", 1, "workload")

        if snapshot.probe_errors:
            add("resource_probe_degraded", 1, "telemetry")

        if self._trend_is_worsening(snapshot):
            add("resource_trend_worsening", 1, "trend")

        base_resource_risk = score > 0
        execution_active = (
            context.open_positions > 0
            or context.execution_mode.upper() in self._ACTIVE_EXECUTION_MODES
        )
        if base_resource_risk and execution_active:
            add("active_execution_context", 1, "execution_context")

        oom_kill_detected = self._new_oom_kill(snapshot)
        if oom_kill_detected:
            add("oom_kill_detected", 100, "headroom")

        return _RawAssessment(
            score=score,
            reasons=tuple(reasons),
            dimensions=frozenset(dimensions),
            has_critical_headroom=critical_headroom,
            oom_kill_detected=oom_kill_detected,
        )

    def _state_from(self, raw: _RawAssessment) -> PressureState:
        if raw.oom_kill_detected:
            return PressureState.CRITICAL
        # A near-exhausted resource becomes CRITICAL only when another
        # independent dimension corroborates the problem. This keeps the
        # classifier from being a disguised single-threshold switch.
        if raw.has_critical_headroom and len(raw.dimensions) >= 2:
            return PressureState.CRITICAL
        if raw.score >= self.policy.pressure_score and len(raw.dimensions) >= 2:
            return PressureState.PRESSURE
        if raw.score > 0:
            return PressureState.WATCH
        return PressureState.NORMAL

    def _apply_hysteresis(
        self,
        snapshot: ResourceSnapshot,
        candidate: PressureState,
    ) -> PressureState:
        now = snapshot.collected_at

        if candidate is not PressureState.NORMAL:
            self._recovery_started_at = None
            return candidate

        if self._state in {PressureState.PRESSURE, PressureState.CRITICAL}:
            self._recovery_started_at = now
            return PressureState.RECOVERING

        if self._state is PressureState.RECOVERING:
            started = self._recovery_started_at
            if started is None or now < started:
                self._recovery_started_at = now
                return PressureState.RECOVERING
            if now - started >= self.policy.recovery_window:
                self._recovery_started_at = None
                return PressureState.NORMAL
            return PressureState.RECOVERING

        self._recovery_started_at = None
        return PressureState.NORMAL

    def _new_oom_kill(self, snapshot: ResourceSnapshot) -> bool:
        previous = self._previous
        if previous is None:
            return False
        return (
            snapshot.system.cgroup_oom_kills
            > previous.system.cgroup_oom_kills
        )

    def _trend_is_worsening(self, snapshot: ResourceSnapshot) -> bool:
        previous = self._previous
        if previous is None or snapshot.collected_at <= previous.collected_at:
            return False

        current = snapshot.system
        before = previous.system
        ratio_pairs = (
            (
                _ratio(current.memory_used_bytes, current.memory_limit_bytes),
                _ratio(before.memory_used_bytes, before.memory_limit_bytes),
                self.policy.memory_trend_delta,
            ),
            (
                _ratio(current.disk_used_bytes, current.disk_total_bytes),
                _ratio(before.disk_used_bytes, before.disk_total_bytes),
                self.policy.disk_trend_delta,
            ),
            (
                _ratio(current.inode_used, current.inode_total),
                _ratio(before.inode_used, before.inode_total),
                self.policy.inode_trend_delta,
            ),
        )
        if any(
            now is not None
            and old is not None
            and now - old >= threshold
            for now, old, threshold in ratio_pairs
        ):
            return True
        if (
            snapshot.postgres.scheduler_lag_seconds
            - previous.postgres.scheduler_lag_seconds
            >= self.policy.scheduler_trend_delta_seconds
        ):
            return True
        return (
            snapshot.redis.execution_queue_lag_seconds
            - previous.redis.execution_queue_lag_seconds
            >= self.policy.queue_trend_delta_seconds
        )


def _ratio(used: int, capacity: int) -> float | None:
    if capacity <= 0 or used < 0:
        return None
    return max(0.0, used / capacity)


def _headroom_signal(
    ratio: float | None,
    *,
    watch: float,
    pressure: float,
    critical: float,
    name: str,
) -> tuple[int, str | None, bool]:
    if ratio is None:
        return 0, None, False
    if ratio >= critical:
        return 3, f"{name}_headroom_critical", True
    if ratio >= pressure:
        return 2, f"{name}_headroom_pressure", False
    if ratio >= watch:
        return 1, f"{name}_headroom_watch", False
    return 0, None, False


__all__ = [
    "PressureAssessment",
    "PressureClassifier",
    "PressureContext",
    "PressurePolicy",
    "PressureState",
]
