from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.ops.backpressure import (
    BackpressureContext,
    EntryDisposition,
    WorkloadDisposition,
    WorkloadKind,
    build_backpressure_plan,
)
from app.ops.forecast import (
    ForecastHorizon,
    ForecastQuality,
    TimeToThresholdForecast,
)
from app.ops.pressure import PressureState


NOW = datetime(2026, 8, 18, 17, 0, tzinfo=UTC)


def _forecast(
    horizon: ForecastHorizon,
    *,
    quality: ForecastQuality = ForecastQuality.STABLE,
    confidence: float = 0.9,
) -> TimeToThresholdForecast:
    seconds = {
        ForecastHorizon.CRITICAL: 30 * 60.0,
        ForecastHorizon.WARNING: 4 * 3600.0,
        ForecastHorizon.NONE: None,
    }[horizon]
    return TimeToThresholdForecast(
        quality=quality,
        confidence=confidence,
        slope_per_second=0.01 if seconds is not None else None,
        seconds_to_threshold=seconds,
        predicted_at=NOW + timedelta(seconds=seconds) if seconds is not None else None,
        horizon=horizon,
        sample_count=8,
    )


def test_normal_and_watch_do_not_apply_backpressure():
    for state in (PressureState.NORMAL, PressureState.WATCH):
        plan = build_backpressure_plan(state=state)

        assert plan.effective_state is state
        assert plan.new_entries is EntryDisposition.ALLOW
        assert all(
            disposition is WorkloadDisposition.RUN
            for disposition in plan.workloads.values()
        )


def test_pressure_sheds_only_lower_priority_workloads():
    plan = build_backpressure_plan(state=PressureState.PRESSURE)

    assert plan.new_entries is EntryDisposition.ALLOW
    assert plan.workloads[WorkloadKind.POSITION_PROTECTION] is WorkloadDisposition.RUN
    assert plan.workloads[WorkloadKind.EXIT_RECONCILIATION] is WorkloadDisposition.RUN
    assert plan.workloads[WorkloadKind.SIGNAL_PIPELINE] is WorkloadDisposition.RUN
    assert plan.workloads[WorkloadKind.TELEGRAM_UI] is WorkloadDisposition.THROTTLE
    assert (
        plan.workloads[WorkloadKind.RESEARCH_BACKTEST_REPLAY]
        is WorkloadDisposition.PAUSE
    )
    assert plan.workloads[WorkloadKind.OLLAMA_EXPLAINABILITY] is WorkloadDisposition.SHED


def test_critical_only_advises_halt_new_entries_never_protection_or_exits():
    plan = build_backpressure_plan(
        state=PressureState.CRITICAL,
        context=BackpressureContext(execution_mode="LIVE", open_positions=4),
    )

    assert plan.new_entries is EntryDisposition.HALT_NEW_ENTRIES
    assert plan.workloads[WorkloadKind.POSITION_PROTECTION] is WorkloadDisposition.RUN
    assert plan.workloads[WorkloadKind.EXIT_RECONCILIATION] is WorkloadDisposition.RUN
    assert plan.workloads[WorkloadKind.SIGNAL_PIPELINE] is WorkloadDisposition.RUN
    assert plan.workloads[WorkloadKind.TELEGRAM_UI] is WorkloadDisposition.THROTTLE
    assert (
        plan.workloads[WorkloadKind.RESEARCH_BACKTEST_REPLAY]
        is WorkloadDisposition.PAUSE
    )
    assert plan.workloads[WorkloadKind.OLLAMA_EXPLAINABILITY] is WorkloadDisposition.SHED
    assert "critical_resource_pressure" in plan.reasons
    assert "open_positions_protected" in plan.reasons


def test_recovering_keeps_expensive_work_shed_until_normal_to_prevent_thrash():
    plan = build_backpressure_plan(state=PressureState.RECOVERING)

    assert plan.new_entries is EntryDisposition.ALLOW
    assert plan.workloads[WorkloadKind.POSITION_PROTECTION] is WorkloadDisposition.RUN
    assert plan.workloads[WorkloadKind.EXIT_RECONCILIATION] is WorkloadDisposition.RUN
    assert plan.workloads[WorkloadKind.SIGNAL_PIPELINE] is WorkloadDisposition.RUN
    assert plan.workloads[WorkloadKind.TELEGRAM_UI] is WorkloadDisposition.THROTTLE
    assert (
        plan.workloads[WorkloadKind.RESEARCH_BACKTEST_REPLAY]
        is WorkloadDisposition.PAUSE
    )
    assert plan.workloads[WorkloadKind.OLLAMA_EXPLAINABILITY] is WorkloadDisposition.SHED


def test_high_confidence_critical_tte_escalates_watch_to_proactive_pressure():
    plan = build_backpressure_plan(
        state=PressureState.WATCH,
        forecasts={"memory": _forecast(ForecastHorizon.CRITICAL)},
    )

    assert plan.effective_state is PressureState.PRESSURE
    assert plan.new_entries is EntryDisposition.ALLOW
    assert (
        plan.workloads[WorkloadKind.RESEARCH_BACKTEST_REPLAY]
        is WorkloadDisposition.PAUSE
    )
    assert "critical_tte_forecast:memory" in plan.reasons


def test_warning_or_low_confidence_forecast_does_not_escalate_watch():
    warning = build_backpressure_plan(
        state=PressureState.WATCH,
        forecasts={"disk": _forecast(ForecastHorizon.WARNING)},
    )
    low_confidence = build_backpressure_plan(
        state=PressureState.WATCH,
        forecasts={
            "queue": _forecast(ForecastHorizon.CRITICAL, confidence=0.49),
        },
    )

    for plan in (warning, low_confidence):
        assert plan.effective_state is PressureState.WATCH
        assert all(
            disposition is WorkloadDisposition.RUN
            for disposition in plan.workloads.values()
        )


def test_unstable_or_insufficient_forecast_is_never_used_for_backpressure():
    for quality in (ForecastQuality.UNSTABLE, ForecastQuality.INSUFFICIENT):
        plan = build_backpressure_plan(
            state=PressureState.WATCH,
            forecasts={
                "memory": _forecast(
                    ForecastHorizon.CRITICAL,
                    quality=quality,
                    confidence=0.99,
                )
            },
        )

        assert plan.effective_state is PressureState.WATCH
        assert plan.new_entries is EntryDisposition.ALLOW


def test_forecast_escalation_never_halts_new_entries_by_itself():
    plan = build_backpressure_plan(
        state=PressureState.PRESSURE,
        forecasts={"disk": _forecast(ForecastHorizon.CRITICAL)},
        context=BackpressureContext(execution_mode="CANARY_LIVE", open_positions=2),
    )

    assert plan.effective_state is PressureState.PRESSURE
    assert plan.new_entries is EntryDisposition.ALLOW


def test_critical_workloads_are_invariant_for_every_state_and_context():
    for state in PressureState:
        for positions in (0, 1, 8):
            plan = build_backpressure_plan(
                state=state,
                context=BackpressureContext(
                    execution_mode="LIVE" if positions else "PAPER",
                    open_positions=positions,
                ),
            )
            assert (
                plan.workloads[WorkloadKind.POSITION_PROTECTION]
                is WorkloadDisposition.RUN
            )
            assert (
                plan.workloads[WorkloadKind.EXIT_RECONCILIATION]
                is WorkloadDisposition.RUN
            )


def test_invalid_context_is_rejected():
    try:
        BackpressureContext(execution_mode="PAPER", open_positions=-1)
    except ValueError as exc:
        assert "open_positions" in str(exc)
    else:
        raise AssertionError("negative open positions must be rejected")
