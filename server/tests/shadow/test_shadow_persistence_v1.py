from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import DBAPIError

from app.models import ShadowObservation
from app.models.enums import Direction
from app.shadow.runtime_v1 import ShadowCandidateObservation
from app.shadow.store_v1 import persist_shadow_observations
from app.strategies.result_v2 import DataQualityState


AT = datetime(2026, 8, 20, 20, 0, tzinfo=UTC)


def _observation(*, version: str = "momentum_v2") -> ShadowCandidateObservation:
    return ShadowCandidateObservation(
        observation_key="a" * 64,
        opportunity_key="b" * 64,
        stage="SHADOW",
        instrument_id="BYBIT:BTCUSDT",
        venue="BYBIT",
        strategy_family="MOMENTUM",
        strategy_version=version,
        signal_emitted=True,
        direction=Direction.LONG,
        raw_edge_score=Decimal("0.75"),
        entry_reference=Decimal("100"),
        data_quality_state=DataQualityState.GOOD,
        evaluated_at=AT,
        market_snapshot_hash="c" * 64,
        cost_model_hash="d" * 64,
    )


def test_shadow_observation_persists_measurement_only_fields(session) -> None:
    persisted = persist_shadow_observations(session, (_observation(),))
    session.flush()

    assert len(persisted) == 1
    row = persisted[0]
    assert isinstance(row, ShadowObservation)
    assert row.observation_key == "a" * 64
    assert row.opportunity_key == "b" * 64
    assert row.stage == "SHADOW"
    assert row.strategy_version == "momentum_v2"
    assert row.signal_emitted is True
    assert row.direction == Direction.LONG.value
    assert row.raw_edge_score == Decimal("0.75")
    assert row.entry_reference == Decimal("100")

    for forbidden in (
        "trade_idea_id",
        "paper_trade_id",
        "execution_intent_id",
        "quantity",
        "risk_amount",
        "leverage",
        "order_intent",
        "stop",
        "targets",
        "was_presented",
        "approval",
    ):
        assert not hasattr(row, forbidden)


def test_shadow_persistence_is_idempotent_by_observation_key(session) -> None:
    observation = _observation()
    first = persist_shadow_observations(session, (observation,))
    session.flush()
    second = persist_shadow_observations(session, (observation,))
    session.flush()

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].id == second[0].id
    assert session.query(ShadowObservation).filter_by(observation_key=observation.observation_key).count() == 1


def test_shadow_persistence_is_append_only_at_database_layer(session) -> None:
    row = persist_shadow_observations(session, (_observation(),))[0]
    session.flush()

    with pytest.raises(DBAPIError):
        with session.begin_nested():
            row.strategy_version = "tampered_v9"
            session.flush()
    session.refresh(row)
    assert row.strategy_version == "momentum_v2"

    with pytest.raises(DBAPIError):
        with session.begin_nested():
            session.delete(row)
            session.flush()


def test_no_signal_shadow_observation_persists_without_invented_trade_values(session) -> None:
    observation = ShadowCandidateObservation(
        observation_key="e" * 64,
        opportunity_key="f" * 64,
        stage="SHADOW",
        instrument_id="MOEX:SiU6",
        venue="MOEX",
        strategy_family=None,
        strategy_version="breakout_v2",
        signal_emitted=False,
        direction=None,
        raw_edge_score=None,
        entry_reference=None,
        data_quality_state=None,
        evaluated_at=AT,
        market_snapshot_hash="1" * 64,
        cost_model_hash="2" * 64,
    )

    row = persist_shadow_observations(session, (observation,))[0]
    session.flush()

    assert row.signal_emitted is False
    assert row.direction is None
    assert row.raw_edge_score is None
    assert row.entry_reference is None
    assert row.data_quality_state is None


def test_two_strategies_share_opportunity_identity_but_not_observation_identity() -> None:
    from app.shadow.runtime_v1 import ShadowEvaluationInput, evaluate_shadow_candidates
    from app.strategies.result_v2 import (
        EntryHypothesis,
        ExplanationComponent,
        FeatureProvenance,
        StrategyHorizon,
        StrategyResultV2,
    )

    def candidate(version: str, family: str) -> StrategyResultV2:
        return StrategyResultV2(
            strategy_family=family,
            strategy_version=version,
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

    observations = evaluate_shadow_candidates(
        ShadowEvaluationInput(
            instrument_id="BYBIT:BTCUSDT",
            venue="BYBIT",
            market_snapshot_hash="3" * 64,
            cost_model_hash="4" * 64,
            evaluated_at=AT,
            candidates=(
                candidate("momentum_v2", "MOMENTUM"),
                candidate("breakout_v2", "BREAKOUT"),
            ),
        )
    )

    assert observations[0].opportunity_key == observations[1].opportunity_key
    assert observations[0].observation_key != observations[1].observation_key
