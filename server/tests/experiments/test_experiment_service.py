from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app.experiments.service import ExperimentService
from app.models import (
    Experiment,
    ExperimentArm,
    ExperimentMetric,
    ExperimentRun,
    PromotionDecision,
    StrategyPromotionEvent,
)
from app.models.enums import Strategy
from app.strategies.registry import StrategyDescriptor, StrategyRegistry
from app.strategies.versioning import (
    LEGACY_CONTROL_CONFIG_HASH,
    LEGACY_CONTROL_VERSION,
    StrategyRole,
    TradingStage,
)


BASE = datetime(2026, 8, 18, 12, tzinfo=UTC)
DATASET_ID = "a" * 64
SAME_DATA_HASH = "b" * 64
CANDIDATE_VERSION = "candidate_trend_v2"
CANDIDATE_CONFIG = "c" * 64
COSTS = {
    "maker_fee_bps": "1",
    "taker_fee_bps": "2",
    "entry_slippage_bps": "3",
    "exit_slippage_bps": "4",
    "funding_bps_per_interval": "0.5",
    "spread_bps": "2",
}


def _register_candidate(session) -> None:
    registry = StrategyRegistry(session)
    registry.register(
        StrategyDescriptor(
            family=Strategy.TREND_PULLBACK.value,
            version=CANDIDATE_VERSION,
            role=StrategyRole.CANDIDATE,
            enabled_stages=frozenset(
                {TradingStage.BACKTEST, TradingStage.OOS, TradingStage.SHADOW}
            ),
            config_hash=CANDIDATE_CONFIG,
        ),
        actor="system",
        reason="SAI-010 fixture candidate",
    )


def _service(session) -> ExperimentService:
    _register_candidate(session)
    return ExperimentService(session)


def _experiment(service: ExperimentService):
    return service.create_experiment(
        name="trend-v2 vs legacy",
        control_family=Strategy.TREND_PULLBACK.value,
        control_version=LEGACY_CONTROL_VERSION,
        candidate_family=Strategy.TREND_PULLBACK.value,
        candidate_version=CANDIDATE_VERSION,
        dataset_name="short_horizon_features",
        dataset_snapshot_id=DATASET_ID,
        stage=TradingStage.OOS,
        same_data_hash=SAME_DATA_HASH,
        cost_model=COSTS,
        created_at=BASE,
    )


def test_create_experiment_persists_registered_control_and_candidate_arms(session):
    service = _service(session)
    item = _experiment(service)
    session.flush()

    assert item.control_family == Strategy.TREND_PULLBACK.value
    assert item.control_version == LEGACY_CONTROL_VERSION
    assert item.candidate_version == CANDIDATE_VERSION
    assert item.dataset_snapshot_id == DATASET_ID
    assert item.stage == TradingStage.OOS.value
    assert item.same_data_hash == SAME_DATA_HASH
    assert len(item.cost_model_hash) == 64
    assert item.created_at == BASE

    arms = session.execute(
        select(ExperimentArm)
        .where(ExperimentArm.experiment_id == item.id)
        .order_by(ExperimentArm.arm_role)
    ).scalars().all()
    assert [(arm.arm_role, arm.strategy_version) for arm in arms] == [
        ("CANDIDATE", CANDIDATE_VERSION),
        ("CONTROL", LEGACY_CONTROL_VERSION),
    ]


def test_experiment_rejects_unregistered_or_identical_strategy_versions(session):
    service = ExperimentService(session)

    with pytest.raises(KeyError, match="candidate strategy"):
        service.create_experiment(
            name="missing candidate",
            control_family=Strategy.TREND_PULLBACK.value,
            control_version=LEGACY_CONTROL_VERSION,
            candidate_family=Strategy.TREND_PULLBACK.value,
            candidate_version="not_registered",
            dataset_name="features",
            dataset_snapshot_id=DATASET_ID,
            stage=TradingStage.OOS,
            same_data_hash=SAME_DATA_HASH,
            cost_model=COSTS,
            created_at=BASE,
        )

    with pytest.raises(ValueError, match="control and candidate must differ"):
        service.create_experiment(
            name="same strategy",
            control_family=Strategy.TREND_PULLBACK.value,
            control_version=LEGACY_CONTROL_VERSION,
            candidate_family=Strategy.TREND_PULLBACK.value,
            candidate_version=LEGACY_CONTROL_VERSION,
            dataset_name="features",
            dataset_snapshot_id=DATASET_ID,
            stage=TradingStage.OOS,
            same_data_hash=SAME_DATA_HASH,
            cost_model=COSTS,
            created_at=BASE,
        )


def test_record_run_fails_closed_if_dataset_stage_proof_or_cost_model_differs(session):
    service = _service(session)
    item = _experiment(service)

    common = dict(
        experiment_id=item.id,
        dataset_snapshot_id=DATASET_ID,
        stage=TradingStage.OOS,
        same_data_hash=SAME_DATA_HASH,
        cost_model=COSTS,
        sample_size=120,
        sample_adequate=True,
        result={"control_net_r": "1.0", "candidate_net_r": "1.2"},
        evaluated_at=BASE + timedelta(hours=1),
    )

    with pytest.raises(ValueError, match="dataset snapshot"):
        service.record_run(**{**common, "dataset_snapshot_id": "d" * 64})
    with pytest.raises(ValueError, match="stage"):
        service.record_run(**{**common, "stage": TradingStage.SHADOW})
    with pytest.raises(ValueError, match="same-data proof"):
        service.record_run(**{**common, "same_data_hash": "e" * 64})
    with pytest.raises(ValueError, match="cost model"):
        service.record_run(**{**common, "cost_model": {**COSTS, "spread_bps": "9"}})


def test_run_metric_and_decision_capture_result_sample_and_source_provenance(session):
    service = _service(session)
    item = _experiment(service)
    run = service.record_run(
        experiment_id=item.id,
        dataset_snapshot_id=DATASET_ID,
        stage=TradingStage.OOS,
        same_data_hash=SAME_DATA_HASH,
        cost_model=COSTS,
        sample_size=120,
        sample_adequate=True,
        result={"control_net_r": "1.0", "candidate_net_r": "1.2"},
        evaluated_at=BASE + timedelta(hours=1),
    )
    metric = service.record_metric(
        run.id,
        name="net_expectancy_r",
        control_value=Decimal("0.10"),
        candidate_value=Decimal("0.14"),
        unit="R",
        recorded_at=BASE + timedelta(hours=1, minutes=1),
    )
    decision = service.record_decision(
        experiment_id=item.id,
        run_id=run.id,
        decision="KEEP_CANDIDATE",
        source="OWNER",
        actor="owner",
        reason="continue to shadow",
        decided_at=BASE + timedelta(hours=1, minutes=2),
        detail={"next_stage": "SHADOW"},
    )
    session.flush()

    assert isinstance(run, ExperimentRun)
    assert run.sample_size == 120
    assert run.sample_adequate is True
    assert run.result_json["candidate_net_r"] == "1.2"
    assert metric.delta == Decimal("0.04")
    assert metric.recorded_at == BASE + timedelta(hours=1, minutes=1)
    assert decision.source == "OWNER"
    assert decision.actor == "owner"
    assert decision.decided_at == BASE + timedelta(hours=1, minutes=2)


def test_recording_experiment_decision_does_not_mutate_strategy_registry(session):
    service = _service(session)
    item = _experiment(service)
    run = service.record_run(
        experiment_id=item.id,
        dataset_snapshot_id=DATASET_ID,
        stage=TradingStage.OOS,
        same_data_hash=SAME_DATA_HASH,
        cost_model=COSTS,
        sample_size=120,
        sample_adequate=True,
        result={"candidate_better": True},
        evaluated_at=BASE + timedelta(hours=1),
    )
    before = session.execute(select(func.count(StrategyPromotionEvent.id))).scalar_one()

    service.record_decision(
        experiment_id=item.id,
        run_id=run.id,
        decision="PROMOTION_RECOMMENDED",
        source="AUTOMATIC",
        actor="experiment-service",
        reason="measurement result only",
        decided_at=BASE + timedelta(hours=2),
        detail={},
    )
    session.flush()

    after = session.execute(select(func.count(StrategyPromotionEvent.id))).scalar_one()
    registry = StrategyRegistry(session)
    assert after == before
    assert registry.get(
        Strategy.TREND_PULLBACK.value, CANDIDATE_VERSION
    ).role is StrategyRole.CANDIDATE
    assert registry.get(
        Strategy.TREND_PULLBACK.value, LEGACY_CONTROL_VERSION
    ).role is StrategyRole.CONTROL


def test_experiment_history_is_append_only_in_database(session):
    service = _service(session)
    item = _experiment(service)
    run = service.record_run(
        experiment_id=item.id,
        dataset_snapshot_id=DATASET_ID,
        stage=TradingStage.OOS,
        same_data_hash=SAME_DATA_HASH,
        cost_model=COSTS,
        sample_size=20,
        sample_adequate=False,
        result={"reason": "insufficient sample"},
        evaluated_at=BASE + timedelta(hours=1),
    )
    metric = service.record_metric(
        run.id,
        name="net_expectancy_r",
        control_value=Decimal("0.10"),
        candidate_value=Decimal("0.09"),
        unit="R",
        recorded_at=BASE + timedelta(hours=1, minutes=1),
    )
    decision = service.record_decision(
        experiment_id=item.id,
        run_id=run.id,
        decision="INSUFFICIENT_DATA",
        source="AUTOMATIC",
        actor="experiment-service",
        reason="sample inadequate",
        decided_at=BASE + timedelta(hours=1, minutes=2),
        detail={},
    )
    session.flush()

    for model, row_id, field, replacement in (
        (Experiment, item.id, "name", "rewrite"),
        (ExperimentRun, run.id, "sample_size", 999),
        (ExperimentMetric, metric.id, "unit", "rewritten"),
        (PromotionDecision, decision.id, "reason", "rewritten"),
    ):
        with pytest.raises(DBAPIError):
            with session.begin_nested():
                row = session.get(model, row_id)
                setattr(row, field, replacement)
                session.flush()
        session.expire_all()


def test_experiment_model_contract_is_non_nullable_for_audit_fields():
    required = {
        Experiment: {
            "control_family",
            "control_version",
            "candidate_family",
            "candidate_version",
            "dataset_name",
            "dataset_snapshot_id",
            "stage",
            "same_data_hash",
            "cost_model_hash",
            "cost_model_json",
            "created_at",
        },
        ExperimentRun: {
            "dataset_snapshot_id",
            "stage",
            "same_data_hash",
            "cost_model_hash",
            "cost_model_json",
            "result_json",
            "sample_size",
            "sample_adequate",
            "evaluated_at",
        },
        PromotionDecision: {
            "decision",
            "source",
            "actor",
            "reason",
            "decided_at",
        },
    }
    for model, names in required.items():
        columns = {column.name: column for column in model.__table__.columns}
        assert names <= columns.keys()
        assert all(columns[name].nullable is False for name in names)
