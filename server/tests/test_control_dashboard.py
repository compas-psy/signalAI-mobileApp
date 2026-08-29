from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import (
    BacktestRun,
    ModelRegistry,
    PaperAbDecision,
    PaperAbOutcome,
    ShadowObservation,
)
from tests.conftest import DEVICE_HEADERS


AT = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as c:
        yield c
    app.dependency_overrides.clear()


def _dashboard(client: TestClient, venue: str = "BYBIT") -> dict:
    response = client.get(
        f"/api/v1/control/dashboard?venue={venue}&window_hours=168",
        headers=DEVICE_HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_control_dashboard_empty_database_is_explicit_no_sample(client) -> None:
    body = _dashboard(client)

    assert body["venue"] == "BYBIT"
    assert body["window_hours"] == 168
    assert body["health"] == "NO_SAMPLE"
    assert body["funnel"]["control"]["ideas_created"] == 0
    assert body["funnel"]["candidates"] == []
    assert body["competition"]["candidates"] == []
    assert body["backtest"]["latest"] is None
    assert body["risk_optimizer"]["champion"] is None
    assert body["risk_optimizer"]["config"]["min_samples"] == 80
    assert body["risk_optimizer"]["config"]["cadence_days"] == 7
    assert body["risk_optimizer"]["config"]["absolute_risk_caps_mutable"] is False


def test_control_dashboard_surfaces_dominant_input_failure_as_broken_input(
    client, session
) -> None:
    for index in range(3):
        session.add(
            ShadowObservation(
                observation_key=f"{index + 1:064x}",
                opportunity_key=f"{index + 100:064x}",
                stage="SHADOW",
                instrument_id="CRYPTO:BTCUSDT",
                venue="CRYPTO",
                strategy_family=None,
                strategy_version="crypto_carry_v1",
                evidence_status="INPUT_UNAVAILABLE",
                reason_code="BYBIT_CARRY_FACTS_UNAVAILABLE",
                signal_emitted=False,
                direction=None,
                raw_edge_score=None,
                entry_reference=None,
                data_quality_state=None,
                evaluated_at=AT - timedelta(hours=index),
                market_snapshot_hash="a" * 64,
                cost_model_hash="b" * 64,
            )
        )
    session.flush()

    body = _dashboard(client)
    assert body["health"] == "BROKEN_INPUT"
    candidate = body["competition"]["candidates"][0]
    assert candidate["version"] == "crypto_carry_v1"
    assert candidate["verdict"] == "BROKEN_INPUT"
    assert candidate["shadow"]["observations"] == 3
    assert candidate["shadow"]["unavailable"] == 3
    assert candidate["shadow"]["evaluated"] == 0
    assert candidate["shadow"]["top_unavailable_reasons"] == [
        {"reason": "BYBIT_CARRY_FACTS_UNAVAILABLE", "count": 3}
    ]


def test_pending_paper_ab_outcome_is_not_coerced_to_zero_return(client, session) -> None:
    pair_key = "1" * 64
    control = PaperAbDecision(
        decision_key="2" * 64,
        pair_key=pair_key,
        candidate_version="momentum_v2",
        arm_role="CONTROL",
        strategy_version="legacy_control_v1",
        instrument_id="CRYPTO:BTCUSDT",
        venue="CRYPTO",
        regime="TREND",
        decision_at=AT - timedelta(hours=4),
        market_snapshot_hash="3" * 64,
        cost_model_hash="4" * 64,
        signal_emitted=True,
        direction="LONG",
        entry_reference=Decimal("100"),
        confidence=Decimal("0.60"),
        horizon_minutes=240,
        risk_unit_price=Decimal("5"),
        round_trip_cost_bps=Decimal("10"),
    )
    candidate = PaperAbDecision(
        decision_key="5" * 64,
        pair_key=pair_key,
        candidate_version="momentum_v2",
        arm_role="CANDIDATE",
        strategy_version="momentum_v2",
        instrument_id="CRYPTO:BTCUSDT",
        venue="CRYPTO",
        regime="TREND",
        decision_at=AT - timedelta(hours=4),
        market_snapshot_hash="3" * 64,
        cost_model_hash="4" * 64,
        signal_emitted=True,
        direction="LONG",
        entry_reference=Decimal("100"),
        confidence=Decimal("0.65"),
        horizon_minutes=240,
        risk_unit_price=Decimal("5"),
        round_trip_cost_bps=Decimal("10"),
    )
    session.add_all([control, candidate])
    session.flush()
    session.add(
        PaperAbOutcome(
            decision_id=control.id,
            evidence_status="EVALUATED",
            net_r=Decimal("1.0"),
            exit_reference=Decimal("105"),
            outcome_at=AT,
            reason_code=None,
        )
    )
    session.flush()

    body = _dashboard(client)
    row = next(
        item
        for item in body["competition"]["candidates"]
        if item["version"] == "momentum_v2"
    )
    paper = row["paper"]
    assert paper["control"]["evaluated_outcomes"] == 1
    assert paper["control"]["mean_net_r"] == pytest.approx(1.0)
    assert paper["candidate"]["pending_outcomes"] == 1
    assert paper["candidate"]["evaluated_outcomes"] == 0
    assert paper["candidate"]["mean_net_r"] is None
    assert paper["comparable_pairs"] == 0
    assert paper["candidate_mean_net_r"] is None
    assert row["verdict"] in {"INSUFFICIENT_OUTCOMES", "WAITING_FOR_SAMPLE"}


def test_dashboard_separates_strategy_backtest_from_risk_optimizer(client, session) -> None:
    strategy_run = BacktestRun(
        label="crypto-oos-main",
        strategy="TREND_PULLBACK",
        period_from=date(2025, 1, 1),
        period_to=date(2026, 8, 1),
        config_hash="c" * 64,
        engine_version="2.0.0",
        universe_json=["CRYPTO"],
        trades=240,
        net_return=Decimal("14.0"),
        profit_factor=Decimal("1.42"),
        expectancy_r=Decimal("0.18"),
        max_drawdown=Decimal("3.2"),
        sharpe=Decimal("1.10"),
        sortino=Decimal("1.35"),
        calmar=Decimal("1.20"),
        brier_score=Decimal("0.19"),
        pbo=Decimal("0.12"),
        top5_contribution=Decimal("0.22"),
        report_json={"stage": "OOS"},
        gate_passed=True,
        gate_detail_json={"reason": "passed"},
    )
    risk_run = BacktestRun(
        label="risk-exit-v2:runner_wide",
        strategy=None,
        period_from=date(2026, 1, 1),
        period_to=date(2026, 8, 20),
        config_hash="d" * 64,
        engine_version="2.0.0",
        universe_json=["FORTS", "CRYPTO"],
        trades=96,
        net_return=Decimal("8.0"),
        profit_factor=None,
        expectancy_r=Decimal("0.21"),
        max_drawdown=Decimal("4.1"),
        sharpe=None,
        sortino=None,
        calmar=None,
        brier_score=None,
        pbo=None,
        top5_contribution=Decimal("0.24"),
        report_json={"candidate_id": "runner_wide"},
        gate_passed=True,
        gate_detail_json={"promotion": "passed"},
    )
    champion = ModelRegistry(
        name="risk_exit_policy",
        version="20260820T120000Z",
        role="champion",
        algorithm="bounded_walk_forward_llm_critic_v2",
        trained_from=date(2026, 1, 1),
        trained_to=date(2026, 8, 20),
        sample_size=96,
        oos_brier=None,
        oos_ece=None,
        calibration_json={
            "candidate_id": "runner_wide",
            "metrics": {"expectancy": "0.21", "max_drawdown": "4.1"},
            "llm_review": {"verdict": "pass", "summary": "stable"},
            "absolute_risk_caps_changed": False,
        },
        feature_list=["actual_r", "mfe_r", "risk_policy.mode"],
        approved_by_human=False,
        promoted_at=AT - timedelta(days=2),
    )
    session.add_all([strategy_run, risk_run, champion])
    session.flush()

    body = _dashboard(client)
    latest = body["backtest"]["latest"]
    assert latest["label"] == "crypto-oos-main"
    assert latest["trades"] == 240
    assert latest["profit_factor"] == pytest.approx(1.42)
    assert latest["expectancy_r"] == pytest.approx(0.18)
    assert latest["gate_passed"] is True

    optimizer = body["risk_optimizer"]
    assert optimizer["latest_run"]["label"] == "risk-exit-v2:runner_wide"
    assert optimizer["champion"]["version"] == "20260820T120000Z"
    assert optimizer["champion"]["candidate_id"] == "runner_wide"
    assert optimizer["champion"]["sample_size"] == 96
    assert optimizer["config"]["candidate_ids"] == [
        "baseline",
        "runner_wide",
        "harvest_early",
    ]


def test_control_dashboard_rejects_unknown_external_venue(client) -> None:
    response = client.get(
        "/api/v1/control/dashboard?venue=CRYPTO&window_hours=168",
        headers=DEVICE_HEADERS,
    )
    assert response.status_code == 422
