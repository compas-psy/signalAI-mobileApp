from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.measurement.report import (
    ManualRiskCohort,
    MeasurementDataset,
    StrategyMeasurementRecord,
    build_strategy_measurement_report,
)


BASE = datetime(2026, 8, 20, 9, tzinfo=UTC)


def row(
    input_id: str,
    *,
    minutes: int,
    preset: ManualRiskCohort,
    outcome_r: float,
    auto_risk: float,
    effective_risk: float,
) -> StrategyMeasurementRecord:
    return StrategyMeasurementRecord(
        input_id=input_id,
        timestamp=BASE + timedelta(minutes=minutes),
        dataset=MeasurementDataset.PAPER,
        variant="legacy_control_v1",
        strategy="TREND_PULLBACK",
        instrument_id="BYBIT:BTCUSDT",
        venue="BYBIT",
        regime="TREND",
        outcome_r=outcome_r,
        risk_preset=preset,
        auto_risk_amount=auto_risk,
        effective_risk_amount=effective_risk,
    )


def test_manual_risk_report_keeps_auto_and_boost_cohorts_separate_and_quantifies_size_only_impact():
    report = build_strategy_measurement_report(
        (
            row(
                "auto-win",
                minutes=0,
                preset=ManualRiskCohort.AUTO,
                outcome_r=1.0,
                auto_risk=100.0,
                effective_risk=100.0,
            ),
            row(
                "boost-helped",
                minutes=1,
                preset=ManualRiskCohort.BOOST_1,
                outcome_r=1.5,
                auto_risk=100.0,
                effective_risk=125.0,
            ),
            row(
                "boost-hurt",
                minutes=2,
                preset=ManualRiskCohort.BOOST_1,
                outcome_r=-1.0,
                auto_risk=100.0,
                effective_risk=125.0,
            ),
            row(
                "boost2-helped",
                minutes=3,
                preset=ManualRiskCohort.BOOST_2,
                outcome_r=0.5,
                auto_risk=100.0,
                effective_risk=150.0,
            ),
        ),
        from_time=BASE,
        to_time=BASE + timedelta(hours=1),
        champion="legacy_control_v1",
        candidate="candidate_v2",
        min_sample=1,
    )

    cohorts = report["variants"]["legacy_control_v1"]["manual_risk_cohorts"]
    assert [item["preset"] for item in cohorts] == ["AUTO", "BOOST_1", "BOOST_2"]

    boost_1 = cohorts[1]["datasets"]["PAPER"]
    assert boost_1["usable_sample_size"] == 2
    assert boost_1["impact"] == {
        "sized_sample_size": 2,
        "helped_count": 1,
        "worsened_count": 1,
        "neutral_count": 0,
        "actual_pnl": pytest.approx(62.5),
        "auto_counterfactual_pnl": pytest.approx(50.0),
        "incremental_pnl": pytest.approx(12.5),
        "incremental_max_drawdown": pytest.approx(25.0),
    }

    boost_2 = cohorts[2]["datasets"]["PAPER"]
    assert boost_2["impact"]["helped_count"] == 1
    assert boost_2["impact"]["worsened_count"] == 0
    assert boost_2["impact"]["incremental_pnl"] == pytest.approx(25.0)


def test_missing_sizing_facts_do_not_invent_incremental_pnl():
    record = StrategyMeasurementRecord(
        input_id="legacy-auto",
        timestamp=BASE,
        dataset=MeasurementDataset.PAPER,
        variant="legacy_control_v1",
        strategy="TREND_PULLBACK",
        instrument_id="MOEX:SiU6",
        venue="MOEX",
        regime="TREND",
        outcome_r=0.4,
        risk_preset=ManualRiskCohort.BOOST_1,
    )
    report = build_strategy_measurement_report(
        (record,),
        from_time=BASE,
        to_time=BASE + timedelta(hours=1),
        champion="legacy_control_v1",
        candidate="candidate_v2",
        min_sample=1,
    )

    impact = report["variants"]["legacy_control_v1"]["manual_risk_cohorts"][1]["datasets"]["PAPER"]["impact"]
    assert impact["sized_sample_size"] == 0
    assert impact["incremental_pnl"] is None
    assert impact["incremental_max_drawdown"] is None


def test_manual_risk_cohort_rejects_unknown_preset():
    with pytest.raises(ValueError, match="risk_preset"):
        StrategyMeasurementRecord(
            input_id="bad",
            timestamp=BASE,
            dataset=MeasurementDataset.PAPER,
            variant="legacy_control_v1",
            strategy="TREND_PULLBACK",
            instrument_id="MOEX:SiU6",
            venue="MOEX",
            regime="TREND",
            outcome_r=0.0,
            risk_preset="DOUBLE_OR_NOTHING",
        )
