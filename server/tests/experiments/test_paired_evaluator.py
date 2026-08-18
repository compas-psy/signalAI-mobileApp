from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.experiments.evaluator import ArmObservation, evaluate_paired
from app.measurement.report import MeasurementDataset


BASE = datetime(2026, 8, 18, 10, tzinfo=UTC)
COST_HASH = "c" * 64


def observation(
    opportunity_id: str,
    *,
    minutes: int,
    signal: bool,
    net_r: str | None,
    confidence: str | None = None,
    instrument_id: str = "BYBIT:BTCUSDT",
    market_snapshot_hash: str | None = None,
    cost_model_hash: str = COST_HASH,
    venue: str = "BYBIT",
    regime: str = "TREND",
    label_usable: bool = True,
) -> ArmObservation:
    return ArmObservation(
        opportunity_id=opportunity_id,
        instrument_id=instrument_id,
        decision_at=BASE + timedelta(minutes=minutes),
        market_snapshot_hash=market_snapshot_hash or (opportunity_id[0] * 64),
        cost_model_hash=cost_model_hash,
        venue=venue,
        regime=regime,
        signal_emitted=signal,
        net_r=Decimal(net_r) if net_r is not None else None,
        confidence=Decimal(confidence) if confidence is not None else None,
        label_usable=label_usable,
    )


def sample_rows():
    control = (
        observation("a1", minutes=0, signal=True, net_r="1.0", confidence="0.70"),
        observation("b2", minutes=5, signal=False, net_r=None),
        observation("c3", minutes=10, signal=False, net_r=None),
        observation("d4", minutes=15, signal=True, net_r="0.6", confidence="0.65"),
        observation("e5", minutes=20, signal=True, net_r="-0.7", confidence="0.55"),
    )
    candidate = (
        observation("a1", minutes=0, signal=True, net_r="1.2", confidence="0.80"),
        observation("b2", minutes=5, signal=True, net_r="0.8", confidence="0.75"),
        observation("c3", minutes=10, signal=True, net_r="-0.4", confidence="0.60"),
        observation("d4", minutes=15, signal=False, net_r=None),
        observation("e5", minutes=20, signal=False, net_r=None),
    )
    return control, candidate


def test_paired_evaluator_uses_same_opportunity_universe_and_reports_incremental_metrics():
    control, candidate = sample_rows()

    result = evaluate_paired(
        control,
        candidate,
        control_version="legacy_control_v1",
        candidate_version="candidate_trend_v2",
        dataset=MeasurementDataset.OOS if hasattr(MeasurementDataset, "OOS") else MeasurementDataset.BACKTEST,
        min_sample=5,
    )

    assert result.paired_sample_size == 5
    assert result.paired_usable_sample_size == 5
    assert result.sample_adequate is True
    assert result.incremental_net_expectancy_r == pytest.approx(0.14)
    assert result.incremental_max_drawdown_r is not None
    assert result.hit_rate_delta == pytest.approx(0.0)
    assert result.opportunity_overlap == pytest.approx(0.2)
    assert result.candidate_only_wins == 1
    assert result.candidate_only_losses == 1
    assert result.control_only_wins == 1
    assert result.control_only_losses == 1
    assert len(result.same_data_hash) == 64
    assert result.cost_model_hash == COST_HASH
    assert result.control_version == "legacy_control_v1"
    assert result.candidate_version == "candidate_trend_v2"


def test_pairing_is_order_independent_and_same_data_hash_is_deterministic():
    control, candidate = sample_rows()
    forward = evaluate_paired(
        control,
        candidate,
        control_version="legacy_control_v1",
        candidate_version="candidate_trend_v2",
        dataset=MeasurementDataset.BACKTEST,
        min_sample=1,
    )
    reverse = evaluate_paired(
        tuple(reversed(control)),
        tuple(reversed(candidate)),
        control_version="legacy_control_v1",
        candidate_version="candidate_trend_v2",
        dataset=MeasurementDataset.BACKTEST,
        min_sample=1,
    )

    assert reverse.same_data_hash == forward.same_data_hash
    assert reverse.incremental_net_expectancy_r == forward.incremental_net_expectancy_r
    assert reverse.incremental_max_drawdown_r == forward.incremental_max_drawdown_r


@pytest.mark.parametrize(
    "field, replacement, message",
    [
        ("instrument_id", "BYBIT:ETHUSDT", "instrument"),
        ("decision_at", BASE + timedelta(days=1), "decision timestamp"),
        ("market_snapshot_hash", "f" * 64, "market snapshot"),
        ("cost_model_hash", "d" * 64, "cost model"),
        ("venue", "MOEX", "venue"),
        ("regime", "RANGE", "regime"),
    ],
)
def test_pair_context_mismatch_fails_closed(field, replacement, message):
    control, candidate = sample_rows()
    changed = list(candidate)
    original = changed[0]
    values = {
        "opportunity_id": original.opportunity_id,
        "instrument_id": original.instrument_id,
        "decision_at": original.decision_at,
        "market_snapshot_hash": original.market_snapshot_hash,
        "cost_model_hash": original.cost_model_hash,
        "venue": original.venue,
        "regime": original.regime,
        "signal_emitted": original.signal_emitted,
        "net_r": original.net_r,
        "confidence": original.confidence,
        "label_usable": original.label_usable,
    }
    values[field] = replacement
    changed[0] = ArmObservation(**values)

    with pytest.raises(ValueError, match=message):
        evaluate_paired(
            control,
            tuple(changed),
            control_version="legacy_control_v1",
            candidate_version="candidate_trend_v2",
            dataset=MeasurementDataset.BACKTEST,
            min_sample=1,
        )


def test_missing_counterpart_or_duplicate_opportunity_fails_closed():
    control, candidate = sample_rows()
    with pytest.raises(ValueError, match="same opportunity universe"):
        evaluate_paired(
            control,
            candidate[:-1],
            control_version="legacy_control_v1",
            candidate_version="candidate_trend_v2",
            dataset=MeasurementDataset.BACKTEST,
            min_sample=1,
        )

    with pytest.raises(ValueError, match="duplicate opportunity"):
        evaluate_paired(
            control + (control[0],),
            candidate,
            control_version="legacy_control_v1",
            candidate_version="candidate_trend_v2",
            dataset=MeasurementDataset.BACKTEST,
            min_sample=1,
        )


def test_arm_observation_rejects_inconsistent_signal_outcome_and_bad_provenance():
    with pytest.raises(ValueError, match="signal outcome"):
        observation("a1", minutes=0, signal=False, net_r="1")
    with pytest.raises(ValueError, match="usable emitted signal"):
        observation("a1", minutes=0, signal=True, net_r=None, label_usable=True)
    with pytest.raises(ValueError, match="market_snapshot_hash"):
        observation(
            "a1", minutes=0, signal=True, net_r="1", market_snapshot_hash="short"
        )
    with pytest.raises(ValueError, match="cost_model_hash"):
        observation(
            "a1", minutes=0, signal=True, net_r="1", cost_model_hash="short"
        )


def test_evaluator_requires_one_cost_model_across_entire_experiment_run():
    control, candidate = sample_rows()
    changed = list(candidate)
    original = changed[-1]
    changed[-1] = ArmObservation(
        opportunity_id=original.opportunity_id,
        instrument_id=original.instrument_id,
        decision_at=original.decision_at,
        market_snapshot_hash=original.market_snapshot_hash,
        cost_model_hash="d" * 64,
        venue=original.venue,
        regime=original.regime,
        signal_emitted=original.signal_emitted,
        net_r=original.net_r,
        confidence=original.confidence,
        label_usable=original.label_usable,
    )

    with pytest.raises(ValueError, match="single cost model"):
        evaluate_paired(
            control[:-1] + (ArmObservation(
                opportunity_id=control[-1].opportunity_id,
                instrument_id=control[-1].instrument_id,
                decision_at=control[-1].decision_at,
                market_snapshot_hash=control[-1].market_snapshot_hash,
                cost_model_hash="d" * 64,
                venue=control[-1].venue,
                regime=control[-1].regime,
                signal_emitted=control[-1].signal_emitted,
                net_r=control[-1].net_r,
                confidence=control[-1].confidence,
                label_usable=control[-1].label_usable,
            ),),
            tuple(changed),
            control_version="legacy_control_v1",
            candidate_version="candidate_trend_v2",
            dataset=MeasurementDataset.BACKTEST,
            min_sample=1,
        )
