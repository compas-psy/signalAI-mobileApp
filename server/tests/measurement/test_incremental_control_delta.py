from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.measurement.report import (
    MeasurementDataset,
    StrategyMeasurementRecord,
    build_strategy_measurement_report,
)


BASE = datetime(2026, 8, 18, 10, tzinfo=UTC)


def row(
    opportunity_id: str,
    *,
    minutes: int,
    variant: str,
    signal: bool,
    outcome_r: float,
    confidence: float | None = None,
) -> StrategyMeasurementRecord:
    return StrategyMeasurementRecord(
        input_id=opportunity_id,
        timestamp=BASE + timedelta(minutes=minutes),
        dataset=MeasurementDataset.BACKTEST,
        variant=variant,
        strategy=variant,
        instrument_id="BYBIT:BTCUSDT",
        venue="BYBIT",
        regime="TREND",
        outcome_r=outcome_r,
        confidence=confidence,
        label_usable=True,
        signal_emitted=signal,
    )


def paired_rows() -> tuple[StrategyMeasurementRecord, ...]:
    return (
        row("a1", minutes=0, variant="control", signal=True, outcome_r=1.0, confidence=0.70),
        row("a1", minutes=0, variant="candidate", signal=True, outcome_r=1.2, confidence=0.80),
        row("b2", minutes=5, variant="control", signal=False, outcome_r=0.0),
        row("b2", minutes=5, variant="candidate", signal=True, outcome_r=0.8, confidence=0.75),
        row("c3", minutes=10, variant="control", signal=False, outcome_r=0.0),
        row("c3", minutes=10, variant="candidate", signal=True, outcome_r=-0.4, confidence=0.60),
        row("d4", minutes=15, variant="control", signal=True, outcome_r=0.6, confidence=0.65),
        row("d4", minutes=15, variant="candidate", signal=False, outcome_r=0.0),
        row("e5", minutes=20, variant="control", signal=True, outcome_r=-0.7, confidence=0.55),
        row("e5", minutes=20, variant="candidate", signal=False, outcome_r=0.0),
    )


def report(*, min_sample: int = 5):
    return build_strategy_measurement_report(
        paired_rows(),
        from_time=BASE,
        to_time=BASE + timedelta(hours=1),
        champion="control",
        candidate="candidate",
        min_sample=min_sample,
    )


def test_incremental_control_delta_reports_decision_level_value_and_selection_overlap():
    comparison = report()["comparison"]["datasets"][MeasurementDataset.BACKTEST.value]
    delta = comparison["incremental_control_delta"]

    assert comparison["delta_expectancy_r"] == pytest.approx(0.14)
    assert delta == {
        "control_version": "control",
        "candidate_version": "candidate",
        "paired_sample_size": 5,
        "paired_usable_sample_size": 5,
        "sample_adequate": True,
        "incremental_net_expectancy_r": pytest.approx(0.14),
        "incremental_max_drawdown_r": pytest.approx(-0.3),
        "hit_rate_delta": pytest.approx(0.0),
        "calibration_delta": pytest.approx(-0.05),
        "opportunity_overlap": pytest.approx(0.2),
        "candidate_only_wins": 1,
        "candidate_only_losses": 1,
        "control_only_wins": 1,
        "control_only_losses": 1,
    }


def test_insufficient_sample_keeps_descriptive_overlap_but_suppresses_statistical_deltas():
    comparison = report(min_sample=6)["comparison"]["datasets"][MeasurementDataset.BACKTEST.value]
    delta = comparison["incremental_control_delta"]

    assert comparison["comparable"] is False
    assert delta["sample_adequate"] is False
    assert delta["incremental_net_expectancy_r"] is None
    assert delta["incremental_max_drawdown_r"] is None
    assert delta["hit_rate_delta"] is None
    assert delta["calibration_delta"] is None
    assert delta["opportunity_overlap"] == pytest.approx(0.2)
    assert delta["candidate_only_wins"] == 1
    assert delta["candidate_only_losses"] == 1
    assert delta["control_only_wins"] == 1
    assert delta["control_only_losses"] == 1


def test_signal_emitted_defaults_true_for_existing_measurement_callers():
    legacy = StrategyMeasurementRecord(
        input_id="legacy",
        timestamp=BASE,
        dataset=MeasurementDataset.PAPER,
        variant="legacy_control_v1",
        strategy="TREND_PULLBACK",
        instrument_id="MOEX:SiU6",
        venue="MOEX",
        regime="TREND",
        outcome_r=0.0,
    )
    assert legacy.signal_emitted is True


def test_no_signal_decision_cannot_hide_non_zero_realised_return():
    with pytest.raises(ValueError, match="no-signal"):
        row(
            "invalid",
            minutes=0,
            variant="candidate",
            signal=False,
            outcome_r=0.4,
        )
