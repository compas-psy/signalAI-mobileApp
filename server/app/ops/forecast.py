"""Pure time-to-threshold forecasts for Resource Autopilot telemetry.

The forecaster is intentionally action-free. It estimates exhaustion horizons
from recent numeric resource samples, but it never throttles work, unloads a
model, changes execution mode, or touches trading state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from math import isfinite
from statistics import median


class ForecastQuality(str, Enum):
    INSUFFICIENT = "INSUFFICIENT"
    STABLE = "STABLE"
    UNSTABLE = "UNSTABLE"


class ForecastHorizon(str, Enum):
    NONE = "NONE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class ForecastSample:
    at: datetime
    value: float

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("ForecastSample.at must be timezone-aware")
        if not isfinite(self.value) or self.value < 0:
            raise ValueError("ForecastSample.value must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class ForecastPolicy:
    min_samples: int = 5
    max_samples: int = 30
    ewma_alpha: float = 0.35
    robust_weight: float = 0.70
    opposing_direction_fraction: float = 0.30
    positive_slope_epsilon: float = 1e-12
    warning_horizon: timedelta = timedelta(hours=6)
    critical_horizon: timedelta = timedelta(hours=1)

    def __post_init__(self) -> None:
        if self.min_samples < 3:
            raise ValueError("min_samples must be at least 3")
        if self.max_samples < self.min_samples:
            raise ValueError("max_samples must be >= min_samples")
        if not 0 < self.ewma_alpha <= 1:
            raise ValueError("ewma_alpha must be in (0, 1]")
        if not 0 <= self.robust_weight <= 1:
            raise ValueError("robust_weight must be in [0, 1]")
        if not 0 < self.opposing_direction_fraction < 0.5:
            raise ValueError("opposing_direction_fraction must be in (0, 0.5)")
        if self.positive_slope_epsilon < 0:
            raise ValueError("positive_slope_epsilon must be non-negative")
        if self.critical_horizon <= timedelta(0):
            raise ValueError("critical_horizon must be positive")
        if self.warning_horizon < self.critical_horizon:
            raise ValueError("critical_horizon must be <= warning_horizon")


@dataclass(frozen=True, slots=True)
class TimeToThresholdForecast:
    quality: ForecastQuality
    confidence: float
    slope_per_second: float | None
    seconds_to_threshold: float | None
    predicted_at: datetime | None
    horizon: ForecastHorizon
    sample_count: int


def forecast_time_to_threshold(
    samples: tuple[ForecastSample, ...] | list[ForecastSample],
    *,
    threshold: float,
    policy: ForecastPolicy | None = None,
) -> TimeToThresholdForecast:
    """Estimate time until ``threshold`` from a bounded recent sample window.

    A Theil-Sen-style median pairwise slope supplies the robust trend. Recent
    interval slopes are clipped around that robust centre before EWMA, so one
    spike cannot dominate the forecast. Opposing directional churn marks a
    series unstable and suppresses TTE entirely.
    """

    policy = policy or ForecastPolicy()
    if not isfinite(threshold) or threshold <= 0:
        raise ValueError("threshold must be finite and positive")

    ordered = tuple(samples)
    _validate_timestamps(ordered)
    window = ordered[-policy.max_samples :]
    sample_count = len(window)

    if sample_count < policy.min_samples:
        return TimeToThresholdForecast(
            quality=ForecastQuality.INSUFFICIENT,
            confidence=0.0,
            slope_per_second=None,
            seconds_to_threshold=None,
            predicted_at=None,
            horizon=ForecastHorizon.NONE,
            sample_count=sample_count,
        )

    latest = window[-1]
    if latest.value >= threshold:
        return TimeToThresholdForecast(
            quality=ForecastQuality.STABLE,
            confidence=1.0,
            slope_per_second=None,
            seconds_to_threshold=0.0,
            predicted_at=latest.at,
            horizon=ForecastHorizon.CRITICAL,
            sample_count=sample_count,
        )

    interval_slopes = _interval_slopes(window)
    pairwise_slopes = _pairwise_slopes(window)
    robust_slope = float(median(pairwise_slopes))
    mad = float(median(abs(value - robust_slope) for value in pairwise_slopes))

    positive = sum(value > policy.positive_slope_epsilon for value in interval_slopes)
    negative = sum(value < -policy.positive_slope_epsilon for value in interval_slopes)
    directional_total = max(1, positive + negative)
    positive_fraction = positive / directional_total
    negative_fraction = negative / directional_total
    opposing_churn = (
        positive_fraction >= policy.opposing_direction_fraction
        and negative_fraction >= policy.opposing_direction_fraction
    )

    direction_agreement = max(positive_fraction, negative_fraction)
    if positive == 0 and negative == 0:
        direction_agreement = 1.0
    dispersion = _relative_dispersion(robust_slope, mad, policy)
    confidence = _confidence(
        sample_count=sample_count,
        policy=policy,
        direction_agreement=direction_agreement,
        dispersion=dispersion,
    )

    if opposing_churn:
        return TimeToThresholdForecast(
            quality=ForecastQuality.UNSTABLE,
            confidence=min(confidence, 0.59),
            slope_per_second=robust_slope,
            seconds_to_threshold=None,
            predicted_at=None,
            horizon=ForecastHorizon.NONE,
            sample_count=sample_count,
        )

    clipped = _clip_slopes(interval_slopes, robust_slope, mad, policy)
    ewma_slope = _ewma(clipped, policy.ewma_alpha)
    slope = (
        policy.robust_weight * robust_slope
        + (1.0 - policy.robust_weight) * ewma_slope
    )

    if slope <= policy.positive_slope_epsilon:
        return TimeToThresholdForecast(
            quality=ForecastQuality.STABLE,
            confidence=confidence,
            slope_per_second=slope,
            seconds_to_threshold=None,
            predicted_at=None,
            horizon=ForecastHorizon.NONE,
            sample_count=sample_count,
        )

    seconds = max(0.0, (threshold - latest.value) / slope)
    predicted_at = latest.at + timedelta(seconds=seconds)
    return TimeToThresholdForecast(
        quality=ForecastQuality.STABLE,
        confidence=confidence,
        slope_per_second=slope,
        seconds_to_threshold=seconds,
        predicted_at=predicted_at,
        horizon=_horizon(seconds, policy),
        sample_count=sample_count,
    )


def _validate_timestamps(samples: tuple[ForecastSample, ...]) -> None:
    for previous, current in zip(samples, samples[1:]):
        if current.at <= previous.at:
            raise ValueError("ForecastSample timestamps must be strictly increasing")


def _interval_slopes(samples: tuple[ForecastSample, ...]) -> tuple[float, ...]:
    return tuple(
        (current.value - previous.value)
        / (current.at - previous.at).total_seconds()
        for previous, current in zip(samples, samples[1:])
    )


def _pairwise_slopes(samples: tuple[ForecastSample, ...]) -> tuple[float, ...]:
    slopes: list[float] = []
    for left_index, left in enumerate(samples[:-1]):
        for right in samples[left_index + 1 :]:
            seconds = (right.at - left.at).total_seconds()
            slopes.append((right.value - left.value) / seconds)
    return tuple(slopes)


def _relative_dispersion(
    robust_slope: float,
    mad: float,
    policy: ForecastPolicy,
) -> float:
    denominator = max(abs(robust_slope), policy.positive_slope_epsilon)
    if denominator == 0:
        return 0.0 if mad == 0 else 1.0
    return max(0.0, mad / denominator)


def _confidence(
    *,
    sample_count: int,
    policy: ForecastPolicy,
    direction_agreement: float,
    dispersion: float,
) -> float:
    full_confidence_samples = max(policy.min_samples, min(policy.max_samples, 6))
    sample_factor = min(1.0, sample_count / full_confidence_samples)
    dispersion_factor = 1.0 / (1.0 + dispersion)
    return max(
        0.0,
        min(1.0, sample_factor * direction_agreement * dispersion_factor),
    )


def _clip_slopes(
    slopes: tuple[float, ...],
    centre: float,
    mad: float,
    policy: ForecastPolicy,
) -> tuple[float, ...]:
    # With a perfectly stable trend MAD is zero. Use a narrow scale around the
    # robust slope so a single discontinuity still cannot dominate the EWMA.
    fallback = max(abs(centre) * 0.25, policy.positive_slope_epsilon)
    radius = 3.0 * max(mad, fallback)
    lower = centre - radius
    upper = centre + radius
    return tuple(min(upper, max(lower, value)) for value in slopes)


def _ewma(values: tuple[float, ...], alpha: float) -> float:
    current = values[0]
    for value in values[1:]:
        current = alpha * value + (1.0 - alpha) * current
    return current


def _horizon(seconds: float, policy: ForecastPolicy) -> ForecastHorizon:
    if seconds <= policy.critical_horizon.total_seconds():
        return ForecastHorizon.CRITICAL
    if seconds <= policy.warning_horizon.total_seconds():
        return ForecastHorizon.WARNING
    return ForecastHorizon.NONE


__all__ = [
    "ForecastHorizon",
    "ForecastPolicy",
    "ForecastQuality",
    "ForecastSample",
    "TimeToThresholdForecast",
    "forecast_time_to_threshold",
]
