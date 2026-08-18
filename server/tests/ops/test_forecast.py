from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.ops.forecast import (
    ForecastHorizon,
    ForecastPolicy,
    ForecastQuality,
    ForecastSample,
    forecast_time_to_threshold,
)


T0 = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
HOUR = 3600.0


def _samples(values: list[float], *, step: timedelta = timedelta(hours=1)):
    return tuple(
        ForecastSample(at=T0 + index * step, value=value)
        for index, value in enumerate(values)
    )


def test_insufficient_history_returns_no_forecast():
    result = forecast_time_to_threshold(
        _samples([10.0, 11.0, 12.0]),
        threshold=20.0,
    )

    assert result.quality is ForecastQuality.INSUFFICIENT
    assert result.seconds_to_threshold is None
    assert result.predicted_at is None
    assert result.horizon is ForecastHorizon.NONE
    assert result.confidence == 0.0


def test_stable_linear_growth_predicts_threshold_with_robust_slope():
    result = forecast_time_to_threshold(
        _samples([10.0, 11.0, 12.0, 13.0, 14.0, 15.0]),
        threshold=20.0,
    )

    assert result.quality is ForecastQuality.STABLE
    assert result.slope_per_second == pytest.approx(1 / HOUR, rel=0.03)
    assert result.seconds_to_threshold == pytest.approx(5 * HOUR, rel=0.05)
    assert result.predicted_at == T0 + timedelta(hours=10)
    assert result.horizon is ForecastHorizon.WARNING
    assert result.confidence >= 0.8


def test_critical_horizon_is_one_hour_and_warning_horizon_is_six_hours():
    policy = ForecastPolicy(
        warning_horizon=timedelta(hours=6),
        critical_horizon=timedelta(hours=1),
    )
    samples = _samples([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])

    critical = forecast_time_to_threshold(samples, threshold=15.75, policy=policy)
    warning = forecast_time_to_threshold(samples, threshold=18.0, policy=policy)
    distant = forecast_time_to_threshold(samples, threshold=30.0, policy=policy)

    assert critical.horizon is ForecastHorizon.CRITICAL
    assert warning.horizon is ForecastHorizon.WARNING
    assert distant.horizon is ForecastHorizon.NONE


def test_threshold_already_reached_reports_zero_time_without_extrapolation():
    result = forecast_time_to_threshold(
        _samples([10.0, 11.0, 12.0, 13.0, 14.0, 20.0]),
        threshold=19.0,
    )

    assert result.quality is ForecastQuality.STABLE
    assert result.seconds_to_threshold == 0.0
    assert result.predicted_at == T0 + timedelta(hours=5)
    assert result.horizon is ForecastHorizon.CRITICAL


def test_flat_or_falling_usage_is_stable_but_has_no_exhaustion_time():
    flat = forecast_time_to_threshold(
        _samples([10.0, 10.0, 10.0, 10.0, 10.0, 10.0]),
        threshold=20.0,
    )
    falling = forecast_time_to_threshold(
        _samples([15.0, 14.0, 13.0, 12.0, 11.0, 10.0]),
        threshold=20.0,
    )

    for result in (flat, falling):
        assert result.quality is ForecastQuality.STABLE
        assert result.seconds_to_threshold is None
        assert result.predicted_at is None
        assert result.horizon is ForecastHorizon.NONE


def test_alternating_unstable_series_does_not_emit_forecast():
    result = forecast_time_to_threshold(
        _samples([10.0, 15.0, 10.0, 15.0, 10.0, 15.0, 10.0]),
        threshold=20.0,
    )

    assert result.quality is ForecastQuality.UNSTABLE
    assert result.seconds_to_threshold is None
    assert result.predicted_at is None
    assert result.confidence < 0.6


def test_robust_slope_and_ewma_resist_one_large_outlier():
    result = forecast_time_to_threshold(
        _samples([10.0, 11.0, 12.0, 30.0, 14.0, 15.0, 16.0]),
        threshold=20.0,
    )

    assert result.quality is ForecastQuality.STABLE
    assert result.slope_per_second == pytest.approx(1 / HOUR, rel=0.30)
    assert result.seconds_to_threshold == pytest.approx(4 * HOUR, rel=0.35)
    assert result.confidence > 0.4


def test_rolling_window_ignores_old_regime_outside_max_samples():
    policy = ForecastPolicy(max_samples=6, min_samples=5)
    values = [100.0, 80.0, 60.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0]

    result = forecast_time_to_threshold(
        _samples(values),
        threshold=20.0,
        policy=policy,
    )

    assert result.quality is ForecastQuality.STABLE
    assert result.sample_count == 6
    assert result.slope_per_second == pytest.approx(1 / HOUR, rel=0.05)


def test_forecast_is_resource_agnostic_for_memory_disk_and_queue_values():
    cases = (
        (_samples([60, 62, 64, 66, 68, 70]), 80.0),
        (_samples([700, 710, 720, 730, 740, 750]), 800.0),
        (_samples([1, 2, 3, 4, 5, 6]), 10.0),
    )

    for samples, threshold in cases:
        result = forecast_time_to_threshold(samples, threshold=threshold)
        assert result.quality is ForecastQuality.STABLE
        assert result.seconds_to_threshold is not None
        assert result.seconds_to_threshold > 0


def test_invalid_samples_and_policy_are_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        ForecastSample(at=datetime(2026, 8, 18, 12), value=1.0)
    with pytest.raises(ValueError, match="non-negative"):
        ForecastSample(at=T0, value=-1.0)
    with pytest.raises(ValueError, match="strictly increasing"):
        forecast_time_to_threshold(
            (
                ForecastSample(at=T0, value=1.0),
                ForecastSample(at=T0, value=2.0),
                ForecastSample(at=T0 + timedelta(hours=1), value=3.0),
                ForecastSample(at=T0 + timedelta(hours=2), value=4.0),
                ForecastSample(at=T0 + timedelta(hours=3), value=5.0),
            ),
            threshold=10.0,
        )
    with pytest.raises(ValueError, match="threshold"):
        forecast_time_to_threshold(_samples([1, 2, 3, 4, 5]), threshold=0)
    with pytest.raises(ValueError, match="critical_horizon"):
        ForecastPolicy(
            warning_horizon=timedelta(hours=1),
            critical_horizon=timedelta(hours=2),
        )
