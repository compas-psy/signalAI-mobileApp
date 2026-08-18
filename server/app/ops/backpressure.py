"""Pure workload priority and backpressure policy.

The policy converts measured resource pressure into an immutable advisory plan.
It has no side effects and is intentionally not connected to the scheduler,
trading pipeline, Ollama process control, risk state, or broker execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .forecast import ForecastHorizon, ForecastQuality, TimeToThresholdForecast
from .pressure import PressureState


class WorkloadKind(str, Enum):
    POSITION_PROTECTION = "POSITION_PROTECTION"
    EXIT_RECONCILIATION = "EXIT_RECONCILIATION"
    SIGNAL_PIPELINE = "SIGNAL_PIPELINE"
    TELEGRAM_UI = "TELEGRAM_UI"
    RESEARCH_BACKTEST_REPLAY = "RESEARCH_BACKTEST_REPLAY"
    OLLAMA_EXPLAINABILITY = "OLLAMA_EXPLAINABILITY"


class WorkloadDisposition(str, Enum):
    RUN = "RUN"
    THROTTLE = "THROTTLE"
    PAUSE = "PAUSE"
    SHED = "SHED"


class EntryDisposition(str, Enum):
    ALLOW = "ALLOW"
    HALT_NEW_ENTRIES = "HALT_NEW_ENTRIES"


@dataclass(frozen=True, slots=True)
class BackpressureContext:
    execution_mode: str = "PAPER"
    open_positions: int = 0

    def __post_init__(self) -> None:
        if self.open_positions < 0:
            raise ValueError("open_positions must be non-negative")


@dataclass(frozen=True, slots=True)
class BackpressurePolicy:
    proactive_forecast_confidence: float = 0.60

    def __post_init__(self) -> None:
        if not 0 <= self.proactive_forecast_confidence <= 1:
            raise ValueError("proactive_forecast_confidence must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class BackpressurePlan:
    observed_state: PressureState
    effective_state: PressureState
    workloads: Mapping[WorkloadKind, WorkloadDisposition]
    new_entries: EntryDisposition
    reasons: tuple[str, ...]


_ALL_RUN = MappingProxyType({kind: WorkloadDisposition.RUN for kind in WorkloadKind})


def build_backpressure_plan(
    *,
    state: PressureState,
    forecasts: Mapping[str, TimeToThresholdForecast] | None = None,
    context: BackpressureContext | None = None,
    policy: BackpressurePolicy | None = None,
) -> BackpressurePlan:
    """Build a deterministic advisory plan without applying it.

    Current CRITICAL pressure is the only condition that can recommend
    ``HALT_NEW_ENTRIES``. Forecasts may proactively shed noncritical work, but
    can never halt entries by themselves. Position protection and exit /
    reconciliation are invariant RUN workloads for every possible state.
    """

    context = context or BackpressureContext()
    policy = policy or BackpressurePolicy()
    forecasts = forecasts or {}

    effective_state = state
    reasons: list[str] = []

    if state in {PressureState.NORMAL, PressureState.WATCH}:
        forecast_reason = _critical_forecast_reason(forecasts, policy)
        if forecast_reason is not None:
            effective_state = PressureState.PRESSURE
            reasons.append(forecast_reason)

    if state is PressureState.CRITICAL:
        reasons.append("critical_resource_pressure")
        if context.open_positions > 0:
            reasons.append("open_positions_protected")

    workloads = _workloads_for(effective_state)
    new_entries = (
        EntryDisposition.HALT_NEW_ENTRIES
        if state is PressureState.CRITICAL
        else EntryDisposition.ALLOW
    )

    return BackpressurePlan(
        observed_state=state,
        effective_state=effective_state,
        workloads=MappingProxyType(dict(workloads)),
        new_entries=new_entries,
        reasons=tuple(reasons),
    )


def _critical_forecast_reason(
    forecasts: Mapping[str, TimeToThresholdForecast],
    policy: BackpressurePolicy,
) -> str | None:
    for resource in sorted(forecasts):
        forecast = forecasts[resource]
        if (
            forecast.quality is ForecastQuality.STABLE
            and forecast.horizon is ForecastHorizon.CRITICAL
            and forecast.confidence >= policy.proactive_forecast_confidence
        ):
            return f"critical_tte_forecast:{resource}"
    return None


def _workloads_for(
    state: PressureState,
) -> Mapping[WorkloadKind, WorkloadDisposition]:
    if state in {PressureState.NORMAL, PressureState.WATCH}:
        return _ALL_RUN

    # RECOVERING intentionally keeps expensive work suppressed until the
    # classifier has completed its healthy hysteresis window and returns
    # NORMAL. This prevents rapid shed/restore oscillation.
    if state in {
        PressureState.PRESSURE,
        PressureState.CRITICAL,
        PressureState.RECOVERING,
    }:
        return {
            WorkloadKind.POSITION_PROTECTION: WorkloadDisposition.RUN,
            WorkloadKind.EXIT_RECONCILIATION: WorkloadDisposition.RUN,
            WorkloadKind.SIGNAL_PIPELINE: WorkloadDisposition.RUN,
            WorkloadKind.TELEGRAM_UI: WorkloadDisposition.THROTTLE,
            WorkloadKind.RESEARCH_BACKTEST_REPLAY: WorkloadDisposition.PAUSE,
            WorkloadKind.OLLAMA_EXPLAINABILITY: WorkloadDisposition.SHED,
        }

    raise AssertionError(f"unhandled pressure state: {state}")


__all__ = [
    "BackpressureContext",
    "BackpressurePlan",
    "BackpressurePolicy",
    "EntryDisposition",
    "WorkloadDisposition",
    "WorkloadKind",
    "build_backpressure_plan",
]
