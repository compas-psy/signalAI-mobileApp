"""P0.5 strategy measurement contract: deterministic, paired and dataset-safe."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.measurement.report import (
    MeasurementDataset,
    StrategyMeasurementRecord,
    build_strategy_measurement_report,
)


START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 2, 1, tzinfo=UTC)


def _record(
    *,
    input_id: str,
    variant: str = "champion",
    dataset: MeasurementDataset = MeasurementDataset.PAPER,
    offset: int = 0,
    outcome_r: float = 1.0,
    confidence: float = 0.6,
    strategy: str = "TREND_PULLBACK",
    instrument_id: str = "MOEX:FUT:SIU6",
    venue: str = "MOEX",
    regime: str = "UPTREND",
    mfe_r: float = 1.4,
    mae_r: float = -0.3,
    entry_deviation_r: float = 0.1,
    exit_deviation_r: float = -0.2,
    operational_failure: bool = False,
    reconciliation_mismatch: bool = False,
    label_usable: bool = True,
) -> StrategyMeasurementRecord:
    return StrategyMeasurementRecord(
        input_id=input_id,
        timestamp=START + timedelta(hours=offset),
        dataset=dataset,
        variant=variant,
        strategy=strategy,
        instrument_id=instrument_id,
        venue=venue,
        regime=regime,
        outcome_r=outcome_r,
        mfe_r=mfe_r,
        mae_r=mae_r,
        entry_deviation_r=entry_deviation_r,
        exit_deviation_r=exit_deviation_r,
        confidence=confidence,
        operational_failure=operational_failure,
        reconciliation_mismatch=reconciliation_mismatch,
        label_usable=label_usable,
    )


def test_report_keeps_all_four_datasets_separate_and_flags_small_samples():
    records = [
        _record(input_id="b1", dataset=MeasurementDataset.BACKTEST, outcome_r=1.0),
        _record(input_id="b2", dataset=MeasurementDataset.BACKTEST, outcome_r=-0.5),
        _record(input_id="p1", dataset=MeasurementDataset.PAPER, outcome_r=0.25),
        _record(input_id="s1", dataset=MeasurementDataset.SANDBOX, outcome_r=0.5),
    ]

    report = build_strategy_measurement_report(
        records,
        from_time=START,
        to_time=END,
        champion="champion",
        candidate="candidate",
        min_sample=2,
    )

    datasets = report["variants"]["champion"]["datasets"]
    assert list(datasets) == ["BACKTEST", "PAPER", "SANDBOX", "LIVE"]
    assert datasets["BACKTEST"]["usable_sample_size"] == 2
    assert datasets["BACKTEST"]["expectancy_r"] == pytest.approx(0.25)
    assert datasets["BACKTEST"]["win_rate"] == pytest.approx(0.5)
    assert datasets["BACKTEST"]["sufficient_sample"] is True
    assert datasets["PAPER"]["sufficient_sample"] is False
    assert datasets["LIVE"]["usable_sample_size"] == 0
    assert datasets["LIVE"]["expectancy_r"] is None

    assert report["variants"]["champion"]["by_strategy"][0]["key"] == "TREND_PULLBACK"
    assert report["variants"]["champion"]["by_instrument"][0]["key"] == "MOEX:FUT:SIU6"
    assert report["variants"]["champion"]["by_venue"][0]["key"] == "MOEX"
    assert report["variants"]["champion"]["by_regime"][0]["key"] == "UPTREND"


def test_report_calculates_mfe_mae_deviation_failure_rates_and_unusable_labels():
    records = [
        _record(
            input_id="a",
            outcome_r=1.0,
            mfe_r=2.0,
            mae_r=-0.2,
            entry_deviation_r=-0.1,
            exit_deviation_r=0.3,
            operational_failure=True,
        ),
        _record(
            input_id="b",
            offset=1,
            outcome_r=-1.0,
            mfe_r=0.4,
            mae_r=-1.2,
            entry_deviation_r=0.3,
            exit_deviation_r=-0.1,
            reconciliation_mismatch=True,
        ),
        _record(
            input_id="c",
            offset=2,
            outcome_r=5.0,
            label_usable=False,
            operational_failure=True,
            reconciliation_mismatch=True,
        ),
    ]

    metrics = build_strategy_measurement_report(
        records,
        from_time=START,
        to_time=END,
        champion="champion",
        candidate="candidate",
        min_sample=1,
    )["variants"]["champion"]["datasets"]["PAPER"]

    assert metrics["usable_sample_size"] == 2
    assert metrics["unusable_label_count"] == 1
    assert metrics["avg_mfe_r"] == pytest.approx(1.2)
    assert metrics["avg_mae_r"] == pytest.approx(-0.7)
    assert metrics["avg_entry_deviation_r"] == pytest.approx(0.2)
    assert metrics["avg_exit_deviation_r"] == pytest.approx(0.2)
    assert metrics["operational_failure_rate"] == pytest.approx(2 / 3)
    assert metrics["reconciliation_mismatch_rate"] == pytest.approx(2 / 3)


def test_drawdown_and_recovery_are_based_on_ordered_cumulative_r():
    records = [
        _record(input_id="1", offset=1, outcome_r=2.0),
        _record(input_id="2", offset=2, outcome_r=-1.0),
        _record(input_id="3", offset=3, outcome_r=-1.5),
        _record(input_id="4", offset=4, outcome_r=1.0),
        _record(input_id="5", offset=5, outcome_r=2.0),
    ]

    metrics = build_strategy_measurement_report(
        records,
        from_time=START,
        to_time=END,
        champion="champion",
        candidate="candidate",
        min_sample=1,
    )["variants"]["champion"]["datasets"]["PAPER"]

    assert metrics["max_drawdown_r"] == pytest.approx(2.5)
    assert metrics["max_recovery_trades"] == 4


def test_confidence_calibration_uses_fixed_deciles_and_usable_labels_only():
    records = [
        _record(input_id="a", confidence=0.61, outcome_r=1.0),
        _record(input_id="b", confidence=0.69, outcome_r=-1.0, offset=1),
        _record(input_id="c", confidence=0.95, outcome_r=1.0, offset=2),
        _record(
            input_id="ignored",
            confidence=0.65,
            outcome_r=1.0,
            offset=3,
            label_usable=False,
        ),
    ]

    calibration = build_strategy_measurement_report(
        records,
        from_time=START,
        to_time=END,
        champion="champion",
        candidate="candidate",
        min_sample=1,
    )["variants"]["champion"]["datasets"]["PAPER"]["confidence_calibration"]

    bucket_06 = next(item for item in calibration if item["bucket"] == "0.6-0.7")
    bucket_09 = next(item for item in calibration if item["bucket"] == "0.9-1.0")
    assert bucket_06 == {
        "bucket": "0.6-0.7",
        "count": 2,
        "mean_confidence": pytest.approx(0.65),
        "observed_win_rate": pytest.approx(0.5),
        "absolute_error": pytest.approx(0.15),
    }
    assert bucket_09["count"] == 1
    assert bucket_09["observed_win_rate"] == pytest.approx(1.0)


def test_champion_candidate_comparison_uses_common_inputs_only():
    records = [
        _record(input_id="common-1", variant="champion", outcome_r=1.0),
        _record(input_id="common-2", variant="champion", outcome_r=-1.0, offset=1),
        _record(input_id="champion-only", variant="champion", outcome_r=10.0, offset=2),
        _record(input_id="common-1", variant="candidate", outcome_r=2.0),
        _record(input_id="common-2", variant="candidate", outcome_r=1.0, offset=1),
        _record(input_id="candidate-only", variant="candidate", outcome_r=-10.0, offset=2),
    ]

    comparison = build_strategy_measurement_report(
        records,
        from_time=START,
        to_time=END,
        champion="champion",
        candidate="candidate",
        min_sample=2,
    )["comparison"]

    paper = comparison["datasets"]["PAPER"]
    assert paper["paired_sample_size"] == 2
    assert paper["champion_only_count"] == 1
    assert paper["candidate_only_count"] == 1
    assert paper["champion"]["expectancy_r"] == pytest.approx(0.0)
    assert paper["candidate"]["expectancy_r"] == pytest.approx(1.5)
    assert paper["delta_expectancy_r"] == pytest.approx(1.5)
    assert paper["comparable"] is True


def test_period_is_half_open_and_report_is_deterministic():
    records = [
        _record(input_id="before", offset=-1, outcome_r=100),
        _record(input_id="inside", offset=0, outcome_r=1),
        StrategyMeasurementRecord(
            **{
                **_record(input_id="at-end").__dict__,
                "timestamp": END,
                "outcome_r": 100,
            }
        ),
    ]

    kwargs = dict(
        from_time=START,
        to_time=END,
        champion="champion",
        candidate="candidate",
        min_sample=1,
    )
    first = build_strategy_measurement_report(records, **kwargs)
    second = build_strategy_measurement_report(list(reversed(records)), **kwargs)

    assert first == second
    assert first["variants"]["champion"]["datasets"]["PAPER"]["expectancy_r"] == 1.0
    assert first["period"] == {
        "from": START.isoformat(),
        "to": END.isoformat(),
        "closed": "[from,to)",
    }


def test_duplicate_variant_dataset_input_id_is_rejected():
    records = [
        _record(input_id="same"),
        _record(input_id="same", offset=1),
    ]

    with pytest.raises(ValueError, match="duplicate measurement record"):
        build_strategy_measurement_report(
            records,
            from_time=START,
            to_time=END,
            champion="champion",
            candidate="candidate",
            min_sample=1,
        )
