from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.shadow.runtime_v1 import (
    ShadowEvidenceStatus,
    ShadowEvaluationInput,
    evaluate_shadow_candidates,
)


AT = datetime(2026, 8, 20, 20, 0, tzinfo=UTC)


def test_unavailable_input_is_not_misreported_as_evaluated_no_signal() -> None:
    result = evaluate_shadow_candidates(
        ShadowEvaluationInput(
            instrument_id="CRYPTO:BTCUSDT",
            venue="CRYPTO",
            market_snapshot_hash="a" * 64,
            cost_model_hash="b" * 64,
            evaluated_at=AT,
            candidate_versions=("momentum_v2", "crypto_carry_v1"),
            candidates=(),
            unavailable_reasons=(("crypto_carry_v1", "FUNDING_FACTS_UNAVAILABLE"),),
        )
    )
    by_version = {item.strategy_version: item for item in result}

    assert by_version["momentum_v2"].evidence_status is ShadowEvidenceStatus.EVALUATED
    assert by_version["momentum_v2"].signal_emitted is False
    assert by_version["momentum_v2"].reason_code is None

    carry = by_version["crypto_carry_v1"]
    assert carry.evidence_status is ShadowEvidenceStatus.INPUT_UNAVAILABLE
    assert carry.signal_emitted is False
    assert carry.reason_code == "FUNDING_FACTS_UNAVAILABLE"


def test_unavailable_candidate_cannot_also_have_emitted_output() -> None:
    from decimal import Decimal

    from app.models.enums import Direction
    from app.strategies.result_v2 import (
        DataQualityState,
        EntryHypothesis,
        ExplanationComponent,
        FeatureProvenance,
        StrategyHorizon,
        StrategyResultV2,
    )

    candidate = StrategyResultV2(
        strategy_family="MOMENTUM",
        strategy_version="momentum_v2",
        direction=Direction.LONG,
        raw_edge_score=Decimal("0.5"),
        entry_hypothesis=EntryHypothesis(
            kind="SHADOW_ONLY", reference=Decimal("100"), rationale="fixture"
        ),
        invalidation="fixture",
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
                name="fixture", contribution=Decimal("0.5"), detail="fixture"
            ),
        ),
        evaluated_at=AT,
    )

    with pytest.raises(ValueError, match="unavailable.*candidate output"):
        evaluate_shadow_candidates(
            ShadowEvaluationInput(
                instrument_id="CRYPTO:BTCUSDT",
                venue="CRYPTO",
                market_snapshot_hash="a" * 64,
                cost_model_hash="b" * 64,
                evaluated_at=AT,
                candidate_versions=("momentum_v2",),
                candidates=(candidate,),
                unavailable_reasons=(("momentum_v2", "DATA_UNAVAILABLE"),),
            )
        )


def test_unavailable_reason_must_belong_to_declared_manifest() -> None:
    with pytest.raises(ValueError, match="candidate manifest"):
        evaluate_shadow_candidates(
            ShadowEvaluationInput(
                instrument_id="CRYPTO:BTCUSDT",
                venue="CRYPTO",
                market_snapshot_hash="a" * 64,
                cost_model_hash="b" * 64,
                evaluated_at=AT,
                candidate_versions=("momentum_v2",),
                candidates=(),
                unavailable_reasons=(("crypto_carry_v1", "FUNDING_FACTS_UNAVAILABLE"),),
            )
        )


def test_duplicate_unavailable_reason_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate unavailable"):
        ShadowEvaluationInput(
            instrument_id="CRYPTO:BTCUSDT",
            venue="CRYPTO",
            market_snapshot_hash="a" * 64,
            cost_model_hash="b" * 64,
            evaluated_at=AT,
            candidate_versions=("crypto_carry_v1",),
            candidates=(),
            unavailable_reasons=(
                ("crypto_carry_v1", "FUNDING_FACTS_UNAVAILABLE"),
                ("crypto_carry_v1", "COST_FACTS_UNAVAILABLE"),
            ),
        )
