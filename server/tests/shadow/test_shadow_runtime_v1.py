from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.shadow.runtime_v1 import (
    ShadowCandidateObservation,
    ShadowEvaluationInput,
    evaluate_shadow_candidates,
)
from app.strategies.result_v2 import (
    DataQualityState,
    EntryHypothesis,
    ExplanationComponent,
    FeatureProvenance,
    StrategyHorizon,
    StrategyResultV2,
)
from app.models.enums import Direction


AT = datetime(2026, 8, 20, 20, 0, tzinfo=UTC)


def _candidate(version: str, *, family: str = "MOMENTUM") -> StrategyResultV2:
    return StrategyResultV2(
        strategy_family=family,
        strategy_version=version,
        direction=Direction.LONG,
        raw_edge_score=Decimal("0.75"),
        entry_hypothesis=EntryHypothesis(
            kind="SHADOW_ONLY",
            reference=Decimal("100"),
            rationale="fixture candidate",
        ),
        invalidation="fixture invalidation",
        horizon=StrategyHorizon(value=12, unit="HOURS"),
        feature_provenance=(
            FeatureProvenance(
                name="fixture",
                value="1",
                source="test",
                observed_at=AT,
                tradable_at=AT,
            ),
        ),
        regime_compatibility=("TREND",),
        data_quality_state=DataQualityState.GOOD,
        explanation_components=(
            ExplanationComponent(
                name="fixture",
                contribution=Decimal("0.75"),
                detail="fixture",
            ),
        ),
        evaluated_at=AT,
    )


def test_shadow_runtime_records_candidate_evidence_without_execution_semantics() -> None:
    result = evaluate_shadow_candidates(
        ShadowEvaluationInput(
            instrument_id="BYBIT:BTCUSDT",
            venue="BYBIT",
            market_snapshot_hash="a" * 64,
            cost_model_hash="b" * 64,
            evaluated_at=AT,
            candidates=(
                _candidate("momentum_v2"),
                _candidate("breakout_v2", family="BREAKOUT"),
            ),
        )
    )

    assert [item.strategy_version for item in result] == [
        "momentum_v2",
        "breakout_v2",
    ]
    assert all(isinstance(item, ShadowCandidateObservation) for item in result)
    assert all(item.stage == "SHADOW" for item in result)
    assert all(item.instrument_id == "BYBIT:BTCUSDT" for item in result)
    assert all(item.market_snapshot_hash == "a" * 64 for item in result)
    assert all(item.cost_model_hash == "b" * 64 for item in result)

    for item in result:
        for forbidden in (
            "quantity",
            "risk_amount",
            "leverage",
            "order_intent",
            "stop",
            "targets",
            "paper_trade",
            "notification",
            "approval",
            "execution_mode",
        ):
            assert not hasattr(item, forbidden)


def test_shadow_observation_is_deterministically_idempotent_for_same_evaluation() -> None:
    payload = ShadowEvaluationInput(
        instrument_id="BYBIT:BTCUSDT",
        venue="BYBIT",
        market_snapshot_hash="a" * 64,
        cost_model_hash="b" * 64,
        evaluated_at=AT,
        candidates=(_candidate("momentum_v2"),),
    )

    first = evaluate_shadow_candidates(payload)[0]
    second = evaluate_shadow_candidates(payload)[0]

    assert first.observation_key == second.observation_key


def test_shadow_runtime_rejects_future_candidate_evidence() -> None:
    future = datetime(2026, 8, 20, 20, 1, tzinfo=UTC)
    candidate = StrategyResultV2(
        strategy_family="MOMENTUM",
        strategy_version="momentum_v2",
        direction=Direction.LONG,
        raw_edge_score=Decimal("0.75"),
        entry_hypothesis=EntryHypothesis(
            kind="SHADOW_ONLY",
            reference=Decimal("100"),
            rationale="fixture candidate",
        ),
        invalidation="fixture invalidation",
        horizon=StrategyHorizon(value=12, unit="HOURS"),
        feature_provenance=(
            FeatureProvenance(
                name="fixture",
                value="1",
                source="test",
                observed_at=future,
                tradable_at=future,
            ),
        ),
        regime_compatibility=("TREND",),
        data_quality_state=DataQualityState.GOOD,
        explanation_components=(
            ExplanationComponent(
                name="fixture",
                contribution=Decimal("0.75"),
                detail="fixture",
            ),
        ),
        evaluated_at=future,
    )

    import pytest

    with pytest.raises(ValueError, match="future"):
        evaluate_shadow_candidates(
            ShadowEvaluationInput(
                instrument_id="BYBIT:BTCUSDT",
                venue="BYBIT",
                market_snapshot_hash="a" * 64,
                cost_model_hash="b" * 64,
                evaluated_at=AT,
                candidates=(candidate,),
            )
        )


def test_shadow_runtime_rejects_duplicate_strategy_version_in_same_market_snapshot() -> None:
    import pytest

    candidate = _candidate("momentum_v2")
    with pytest.raises(ValueError, match="duplicate strategy"):
        evaluate_shadow_candidates(
            ShadowEvaluationInput(
                instrument_id="BYBIT:BTCUSDT",
                venue="BYBIT",
                market_snapshot_hash="a" * 64,
                cost_model_hash="b" * 64,
                evaluated_at=AT,
                candidates=(candidate, candidate),
            )
        )


def test_shadow_runtime_allows_no_signal_observation_as_explicit_absence() -> None:
    result = evaluate_shadow_candidates(
        ShadowEvaluationInput(
            instrument_id="MOEX:SiU6",
            venue="MOEX",
            market_snapshot_hash="c" * 64,
            cost_model_hash="d" * 64,
            evaluated_at=AT,
            candidate_versions=("momentum_v2", "breakout_v2"),
            candidates=(),
        )
    )

    assert len(result) == 2
    assert {item.strategy_version for item in result} == {"momentum_v2", "breakout_v2"}
    assert all(item.signal_emitted is False for item in result)
    assert all(item.raw_edge_score is None for item in result)


def test_shadow_input_rejects_unknown_candidate_output_not_declared_in_manifest() -> None:
    import pytest

    with pytest.raises(ValueError, match="candidate manifest"):
        evaluate_shadow_candidates(
            ShadowEvaluationInput(
                instrument_id="BYBIT:BTCUSDT",
                venue="BYBIT",
                market_snapshot_hash="a" * 64,
                cost_model_hash="b" * 64,
                evaluated_at=AT,
                candidate_versions=("breakout_v2",),
                candidates=(_candidate("momentum_v2"),),
            )
        )
