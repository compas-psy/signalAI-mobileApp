from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.backtest.multiple_testing import MultipleTestingRegistry
from app.models import ResearchSearchCampaign, ResearchTrial, ResearchTrialOutcome


BASE = datetime(2026, 8, 18, 9, tzinfo=UTC)


def registry(session) -> MultipleTestingRegistry:
    return MultipleTestingRegistry(session)


def campaign(reg: MultipleTestingRegistry, *, planned: int = 3):
    return reg.create_campaign(
        hypothesis_id="hyp-breakout-volume-v2",
        dataset_name="short_horizon_features",
        dataset_snapshot_id="a" * 64,
        strategy_family="BREAKOUT_RETEST",
        strategy_version="candidate_breakout_v2",
        config_hash="b" * 64,
        planned_variant_count=planned,
        started_at=BASE,
    )


def test_campaign_predeclares_search_size_and_dataset_identity(session):
    reg = registry(session)
    item = campaign(reg, planned=7)
    session.flush()

    assert item.hypothesis_id == "hyp-breakout-volume-v2"
    assert item.dataset_name == "short_horizon_features"
    assert item.dataset_snapshot_id == "a" * 64
    assert item.strategy_version == "candidate_breakout_v2"
    assert item.planned_variant_count == 7
    assert item.started_at == BASE


def test_parameter_hash_is_canonical_and_duplicate_variant_is_idempotent(session):
    reg = registry(session)
    search = campaign(reg)

    first = reg.start_trial(
        search.id,
        parameters={"lookback": 20, "threshold": "1.5", "nested": {"b": 2, "a": 1}},
        started_at=BASE + timedelta(minutes=1),
    )
    second = reg.start_trial(
        search.id,
        parameters={"nested": {"a": 1, "b": 2}, "threshold": "1.5", "lookback": 20},
        started_at=BASE + timedelta(minutes=1),
    )
    session.flush()

    assert second.id == first.id
    assert second.parameter_hash == first.parameter_hash
    assert session.execute(select(ResearchTrial)).scalars().all() == [first]


def test_cannot_register_more_variants_than_predeclared(session):
    reg = registry(session)
    search = campaign(reg, planned=1)
    reg.start_trial(search.id, parameters={"x": 1}, started_at=BASE)

    with pytest.raises(ValueError, match="planned_variant_count"):
        reg.start_trial(search.id, parameters={"x": 2}, started_at=BASE)


def test_each_trial_has_exactly_one_immutable_terminal_outcome(session):
    reg = registry(session)
    search = campaign(reg, planned=1)
    trial = reg.start_trial(search.id, parameters={"x": 1}, started_at=BASE)

    outcome = reg.record_outcome(
        trial.id,
        status="COMPLETED",
        completed_at=BASE + timedelta(hours=1),
        primary_metric=Decimal("0.42"),
        outcome={"net_expectancy_r": "0.42", "max_drawdown": "0.08"},
    )
    session.flush()

    assert outcome.trial_id == trial.id
    assert outcome.primary_metric == Decimal("0.42")

    with pytest.raises(ValueError, match="already has an outcome"):
        reg.record_outcome(
            trial.id,
            status="COMPLETED",
            completed_at=BASE + timedelta(hours=2),
            primary_metric=Decimal("0.99"),
            outcome={"net_expectancy_r": "0.99"},
        )

    outcome.primary_metric = Decimal("99")
    with pytest.raises(DBAPIError):
        session.flush()


def test_failed_trial_is_counted_and_cannot_disappear_from_selection_context(session):
    reg = registry(session)
    search = campaign(reg, planned=3)
    t1 = reg.start_trial(search.id, parameters={"x": 1}, started_at=BASE)
    t2 = reg.start_trial(search.id, parameters={"x": 2}, started_at=BASE)
    t3 = reg.start_trial(search.id, parameters={"x": 3}, started_at=BASE)
    reg.record_outcome(
        t1.id,
        status="COMPLETED",
        completed_at=BASE + timedelta(hours=1),
        primary_metric=Decimal("0.10"),
        outcome={"net_expectancy_r": "0.10"},
    )
    reg.record_outcome(
        t2.id,
        status="FAILED",
        completed_at=BASE + timedelta(hours=1),
        primary_metric=None,
        outcome={"reason": "insufficient fills"},
    )
    reg.record_outcome(
        t3.id,
        status="COMPLETED",
        completed_at=BASE + timedelta(hours=1),
        primary_metric=Decimal("0.30"),
        outcome={"net_expectancy_r": "0.30"},
    )

    evidence = reg.selection_evidence(search.id)

    assert evidence.registered_variants == 3
    assert evidence.completed_variants == 2
    assert evidence.failed_variants == 1
    assert evidence.best_trial_id == t3.id
    assert evidence.best_primary_metric == Decimal("0.30")
    assert evidence.selection_context == "best_of_3_registered_variants"
    assert evidence.promotion_ready is True


def test_incomplete_search_is_explicitly_not_promotion_ready(session):
    reg = registry(session)
    search = campaign(reg, planned=3)
    trial = reg.start_trial(search.id, parameters={"x": 1}, started_at=BASE)
    reg.record_outcome(
        trial.id,
        status="COMPLETED",
        completed_at=BASE + timedelta(hours=1),
        primary_metric=Decimal("0.50"),
        outcome={"net_expectancy_r": "0.50"},
    )

    evidence = reg.selection_evidence(search.id)

    assert evidence.registered_variants == 1
    assert evidence.planned_variants == 3
    assert evidence.selection_context == "best_of_1_registered_variants;planned=3"
    assert evidence.promotion_ready is False
    assert "registered 1/3" in evidence.blockers


def test_pending_outcome_blocks_promotion_even_when_all_variants_registered(session):
    reg = registry(session)
    search = campaign(reg, planned=2)
    t1 = reg.start_trial(search.id, parameters={"x": 1}, started_at=BASE)
    reg.start_trial(search.id, parameters={"x": 2}, started_at=BASE)
    reg.record_outcome(
        t1.id,
        status="COMPLETED",
        completed_at=BASE + timedelta(hours=1),
        primary_metric=Decimal("0.50"),
        outcome={"net_expectancy_r": "0.50"},
    )

    evidence = reg.selection_evidence(search.id)

    assert evidence.promotion_ready is False
    assert "terminal outcomes 1/2" in evidence.blockers


def test_campaign_trial_and_outcome_history_are_append_only_in_database(session):
    reg = registry(session)
    search = campaign(reg, planned=1)
    trial = reg.start_trial(search.id, parameters={"x": 1}, started_at=BASE)
    outcome = reg.record_outcome(
        trial.id,
        status="FAILED",
        completed_at=BASE + timedelta(hours=1),
        primary_metric=None,
        outcome={"reason": "fixture"},
    )
    session.flush()

    search.planned_variant_count = 2
    with pytest.raises(DBAPIError):
        session.flush()
    session.rollback()

    stored_trial = session.get(ResearchTrial, trial.id)
    stored_trial.parameter_json = {"x": 999}
    with pytest.raises(DBAPIError):
        session.flush()
    session.rollback()

    stored_outcome = session.get(ResearchTrialOutcome, outcome.id)
    stored_outcome.outcome_json = {"reason": "rewritten"}
    with pytest.raises(DBAPIError):
        session.flush()
