"""Read-only measurement API builds one reproducible snapshot from stored outcomes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import IdeaOutcome, TradeIdea
from app.models.enums import BarrierOutcome
from tests.conftest import DEVICE_HEADERS, idea_kwargs


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as value:
        yield value
    app.dependency_overrides.clear()


def _idea_outcome(
    session,
    instrument_id: str,
    moment: datetime,
    *,
    engine_version: str,
    input_id: str,
    dataset: str,
    outcome_r: str,
    confidence: str,
    regime: str = "UPTREND",
    operational_failure: bool = False,
    reconciliation_mismatch: bool = False,
) -> TradeIdea:
    idea = TradeIdea(
        **idea_kwargs(
            instrument_id,
            moment,
            engine_version=engine_version,
            confidence=Decimal(confidence),
            status="CLOSED",
            quality_status="ACTIVE",
        )
    )
    session.add(idea)
    session.flush()
    session.add(
        IdeaOutcome(
            idea_id=idea.id,
            barrier_outcome=(
                BarrierOutcome.WIN if Decimal(outcome_r) > 0 else BarrierOutcome.LOSS
            ),
            resolved_at=moment + timedelta(hours=4),
            bars_to_resolution=4,
            model_r=Decimal(outcome_r) if dataset == "BACKTEST" else None,
            ideal_r=Decimal(outcome_r),
            actual_r=Decimal(outcome_r) if dataset != "BACKTEST" else None,
            actual_pnl=None,
            mae_r=Decimal("-0.4"),
            mfe_r=Decimal("1.2"),
            entry_slippage=Decimal("70"),
            exit_slippage=Decimal("35"),
            fees=Decimal("0"),
            funding=Decimal("0"),
            label_usable=True,
            detail_json={
                "measurement_dataset": dataset,
                "measurement_input_id": input_id,
                "measurement_regime": regime,
                "operational_failure": operational_failure,
                "reconciliation_mismatch": reconciliation_mismatch,
            },
        )
    )
    session.flush()
    return idea


def test_measurement_endpoint_requires_device_auth(session):
    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app) as anonymous:
            response = anonymous.get(
                "/api/v1/measurements/strategies",
                params={
                    "from_time": "2026-01-01T00:00:00Z",
                    "to_time": "2026-02-01T00:00:00Z",
                    "champion": "champion-v1",
                    "candidate": "candidate-v2",
                },
            )
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_measurement_endpoint_is_fixed_period_paired_and_reproducible(
    client, session, instrument
):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    _idea_outcome(
        session,
        instrument.instrument_id,
        start + timedelta(days=1),
        engine_version="champion-v1",
        input_id="same-backtest",
        dataset="BACKTEST",
        outcome_r="1.0",
        confidence="0.60",
    )
    _idea_outcome(
        session,
        instrument.instrument_id,
        start + timedelta(days=1),
        engine_version="candidate-v2",
        input_id="same-backtest",
        dataset="BACKTEST",
        outcome_r="2.0",
        confidence="0.70",
    )
    _idea_outcome(
        session,
        instrument.instrument_id,
        start + timedelta(days=2),
        engine_version="champion-v1",
        input_id="same-paper",
        dataset="PAPER",
        outcome_r="-1.0",
        confidence="0.55",
        operational_failure=True,
    )
    _idea_outcome(
        session,
        instrument.instrument_id,
        start + timedelta(days=2),
        engine_version="candidate-v2",
        input_id="same-paper",
        dataset="PAPER",
        outcome_r="0.5",
        confidence="0.65",
        reconciliation_mismatch=True,
    )
    # Outside [from,to): must not affect the report.
    _idea_outcome(
        session,
        instrument.instrument_id,
        datetime(2026, 2, 1, tzinfo=UTC),
        engine_version="candidate-v2",
        input_id="outside",
        dataset="LIVE",
        outcome_r="99",
        confidence="0.99",
    )

    params = {
        "from_time": "2026-01-01T00:00:00Z",
        "to_time": "2026-02-01T00:00:00Z",
        "champion": "champion-v1",
        "candidate": "candidate-v2",
        "min_sample": 1,
    }
    first = client.get("/api/v1/measurements/strategies", params=params)
    second = client.get("/api/v1/measurements/strategies", params=params)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    body = first.json()
    assert body["period"] == {
        "from": "2026-01-01T00:00:00+00:00",
        "to": "2026-02-01T00:00:00+00:00",
        "closed": "[from,to)",
    }
    assert body["unclassified_count"] == 0
    assert body["variants"]["champion-v1"]["datasets"]["BACKTEST"]["expectancy_r"] == 1.0
    assert body["variants"]["candidate-v2"]["datasets"]["PAPER"]["expectancy_r"] == 0.5
    assert body["variants"]["candidate-v2"]["datasets"]["LIVE"]["usable_sample_size"] == 0
    assert body["comparison"]["datasets"]["BACKTEST"]["paired_sample_size"] == 1
    assert body["comparison"]["datasets"]["BACKTEST"]["delta_expectancy_r"] == 1.0
    assert body["comparison"]["datasets"]["PAPER"]["delta_expectancy_r"] == 1.5

    # Slippage is normalized by initial risk: 70 / (90100 - 89400) = 0.1R.
    assert body["variants"]["champion-v1"]["datasets"]["PAPER"]["avg_entry_deviation_r"] == pytest.approx(0.1)


def test_measurement_endpoint_excludes_unclassified_outcomes(client, session, instrument):
    moment = datetime(2026, 1, 10, tzinfo=UTC)
    idea = TradeIdea(
        **idea_kwargs(
            instrument.instrument_id,
            moment,
            engine_version="champion-v1",
            status="CLOSED",
            quality_status="ACTIVE",
        )
    )
    session.add(idea)
    session.flush()
    session.add(
        IdeaOutcome(
            idea_id=idea.id,
            barrier_outcome=BarrierOutcome.WIN,
            resolved_at=moment + timedelta(hours=1),
            model_r=Decimal("1"),
            ideal_r=Decimal("1"),
            actual_r=None,
            label_usable=True,
            detail_json={},
        )
    )
    session.flush()

    response = client.get(
        "/api/v1/measurements/strategies",
        params={
            "from_time": "2026-01-01T00:00:00Z",
            "to_time": "2026-02-01T00:00:00Z",
            "champion": "champion-v1",
            "candidate": "candidate-v2",
            "min_sample": 1,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["unclassified_count"] == 1
    assert body["variants"]["champion-v1"]["datasets"]["BACKTEST"]["usable_sample_size"] == 0


def test_measurement_endpoint_rejects_invalid_period(client):
    response = client.get(
        "/api/v1/measurements/strategies",
        params={
            "from_time": "2026-02-01T00:00:00Z",
            "to_time": "2026-01-01T00:00:00Z",
            "champion": "champion-v1",
            "candidate": "candidate-v2",
        },
    )
    assert response.status_code == 422
