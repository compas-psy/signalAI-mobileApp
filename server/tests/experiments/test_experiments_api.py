from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.experiments.service import ExperimentService
from app.main import app
from app.models.enums import Strategy
from app.strategies.registry import StrategyDescriptor, StrategyRegistry
from app.strategies.versioning import (
    LEGACY_CONTROL_VERSION,
    StrategyRole,
    TradingStage,
)
from tests.conftest import DEVICE_HEADERS


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


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as c:
        yield c
    app.dependency_overrides.clear()


def _register_candidate(session) -> None:
    StrategyRegistry(session).register(
        StrategyDescriptor(
            family=Strategy.TREND_PULLBACK.value,
            version=CANDIDATE_VERSION,
            role=StrategyRole.CANDIDATE,
            enabled_stages=frozenset(
                {TradingStage.BACKTEST, TradingStage.OOS, TradingStage.SHADOW}
            ),
            config_hash=CANDIDATE_CONFIG,
        ),
        actor="test",
        reason="SAI-013 API fixture",
    )


def _seed_experiment(session, *, name: str = "trend-v2 vs legacy", created_at=BASE):
    _register_candidate(session)
    service = ExperimentService(session)
    experiment = service.create_experiment(
        name=name,
        control_family=Strategy.TREND_PULLBACK.value,
        control_version=LEGACY_CONTROL_VERSION,
        candidate_family=Strategy.TREND_PULLBACK.value,
        candidate_version=CANDIDATE_VERSION,
        dataset_name="short_horizon_features",
        dataset_snapshot_id=DATASET_ID,
        stage=TradingStage.OOS,
        same_data_hash=SAME_DATA_HASH,
        cost_model=COSTS,
        created_at=created_at,
    )
    run = service.record_run(
        experiment_id=experiment.id,
        dataset_snapshot_id=DATASET_ID,
        stage=TradingStage.OOS,
        same_data_hash=SAME_DATA_HASH,
        cost_model=COSTS,
        sample_size=120,
        sample_adequate=True,
        result={
            "incremental_control_delta": {
                "incremental_net_expectancy_r": 0.14,
                "opportunity_overlap": 0.72,
            },
            "robustness": {"stress_passed": True},
        },
        evaluated_at=created_at + timedelta(hours=1),
    )
    service.record_metric(
        run.id,
        name="net_expectancy_r",
        control_value=Decimal("0.10"),
        candidate_value=Decimal("0.14"),
        unit="R",
        recorded_at=created_at + timedelta(hours=1, minutes=1),
    )
    service.record_metric(
        run.id,
        name="max_drawdown_r",
        control_value=Decimal("0.70"),
        candidate_value=Decimal("0.40"),
        unit="R",
        recorded_at=created_at + timedelta(hours=1, minutes=2),
    )
    service.record_decision(
        experiment_id=experiment.id,
        run_id=run.id,
        decision="KEEP_CANDIDATE",
        source="OWNER",
        actor="owner",
        reason="continue to shadow",
        decided_at=created_at + timedelta(hours=1, minutes=3),
        detail={"next_stage": "SHADOW"},
    )
    session.flush()
    return experiment, run


def test_experiments_list_is_read_only_summary_with_latest_run(client, session):
    older, _ = _seed_experiment(session, name="older", created_at=BASE)
    newer, newer_run = _seed_experiment(
        session,
        name="newer",
        created_at=BASE + timedelta(days=1),
    )

    response = client.get("/api/v1/experiments?limit=10")
    assert response.status_code == 200
    body = response.json()

    assert body[0]["id"] == str(newer.id)
    assert body[0]["name"] == "newer"
    assert body[0]["control"] == {
        "family": Strategy.TREND_PULLBACK.value,
        "version": LEGACY_CONTROL_VERSION,
    }
    assert body[0]["candidate"] == {
        "family": Strategy.TREND_PULLBACK.value,
        "version": CANDIDATE_VERSION,
    }
    assert body[0]["stage"] == TradingStage.OOS.value
    assert body[0]["dataset_name"] == "short_horizon_features"
    assert body[0]["latest_run"] == {
        "id": str(newer_run.id),
        "evaluated_at": (BASE + timedelta(days=1, hours=1)).isoformat(),
        "sample_size": 120,
        "sample_adequate": True,
    }
    assert body[1]["id"] == str(older.id)


def test_experiment_comparison_exposes_saved_evidence_metrics_and_latest_decision(client, session):
    experiment, run = _seed_experiment(session)

    response = client.get(f"/api/v1/experiments/{experiment.id}/comparison")
    assert response.status_code == 200
    body = response.json()

    assert body["experiment"]["id"] == str(experiment.id)
    assert body["experiment"]["control_version"] == LEGACY_CONTROL_VERSION
    assert body["experiment"]["candidate_version"] == CANDIDATE_VERSION
    assert body["evidence"] == {
        "dataset_name": "short_horizon_features",
        "dataset_snapshot_id": DATASET_ID,
        "stage": TradingStage.OOS.value,
        "same_data_hash": SAME_DATA_HASH,
        "cost_model_hash": experiment.cost_model_hash,
    }
    assert body["latest_run"]["id"] == str(run.id)
    assert body["latest_run"]["sample_adequate"] is True
    assert body["latest_run"]["result"]["incremental_control_delta"][
        "incremental_net_expectancy_r"
    ] == pytest.approx(0.14)

    metrics = {item["name"]: item for item in body["metrics"]}
    assert metrics["net_expectancy_r"]["control_value"] == pytest.approx(0.10)
    assert metrics["net_expectancy_r"]["candidate_value"] == pytest.approx(0.14)
    assert metrics["net_expectancy_r"]["delta"] == pytest.approx(0.04)
    assert metrics["max_drawdown_r"]["delta"] == pytest.approx(-0.30)

    assert body["latest_decision"] == {
        "decision": "KEEP_CANDIDATE",
        "source": "OWNER",
        "actor": "owner",
        "reason": "continue to shadow",
        "detail": {"next_stage": "SHADOW"},
        "decided_at": (BASE + timedelta(hours=1, minutes=3)).isoformat(),
    }


def test_comparison_without_runs_is_valid_pending_state(client, session):
    _register_candidate(session)
    experiment = ExperimentService(session).create_experiment(
        name="pending",
        control_family=Strategy.TREND_PULLBACK.value,
        control_version=LEGACY_CONTROL_VERSION,
        candidate_family=Strategy.TREND_PULLBACK.value,
        candidate_version=CANDIDATE_VERSION,
        dataset_name="features",
        dataset_snapshot_id=DATASET_ID,
        stage=TradingStage.OOS,
        same_data_hash=SAME_DATA_HASH,
        cost_model=COSTS,
        created_at=BASE,
    )

    response = client.get(f"/api/v1/experiments/{experiment.id}/comparison")
    assert response.status_code == 200
    body = response.json()
    assert body["latest_run"] is None
    assert body["metrics"] == []
    assert body["latest_decision"] is None


def test_unknown_experiment_is_404(client):
    response = client.get(
        "/api/v1/experiments/00000000-0000-0000-0000-000000000001/comparison"
    )
    assert response.status_code == 404


def test_experiments_api_has_no_mutating_route(client):
    response = client.post("/api/v1/experiments", json={"decision": "PROMOTE"})
    assert response.status_code == 405
