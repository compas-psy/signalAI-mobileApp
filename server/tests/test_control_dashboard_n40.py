from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import PaperAbDecision, PaperAbOutcome
from tests.conftest import DEVICE_HEADERS


AT = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as c:
        yield c
    app.dependency_overrides.clear()


def _decision(
    *,
    key: str,
    pair_key: str,
    candidate_version: str,
    role: str,
    venue: str,
) -> PaperAbDecision:
    return PaperAbDecision(
        decision_key=key * 64,
        pair_key=pair_key * 64,
        candidate_version=candidate_version,
        arm_role=role,
        strategy_version=("legacy_control_v1" if role == "CONTROL" else candidate_version),
        instrument_id=("CRYPTO:BTCUSDT" if venue in {"CRYPTO", "BYBIT"} else "MOEX:FUT:RIU6"),
        venue=venue,
        regime="TREND",
        decision_at=AT - timedelta(hours=1),
        market_snapshot_hash="a" * 64,
        cost_model_hash="b" * 64,
        signal_emitted=True,
        direction="LONG",
        entry_reference=Decimal("100"),
        confidence=Decimal("0.60"),
        horizon_minutes=240,
        risk_unit_price=Decimal("5"),
        round_trip_cost_bps=Decimal("10"),
    )


def _closed_pair(
    session,
    *,
    pair_seed: str,
    control_key: str,
    candidate_key: str,
    candidate_version: str,
    control_venue: str,
    candidate_venue: str,
    control_r: str = "0.10",
    candidate_r: str = "0.50",
) -> None:
    control = _decision(
        key=control_key,
        pair_key=pair_seed,
        candidate_version=candidate_version,
        role="CONTROL",
        venue=control_venue,
    )
    candidate = _decision(
        key=candidate_key,
        pair_key=pair_seed,
        candidate_version=candidate_version,
        role="CANDIDATE",
        venue=candidate_venue,
    )
    session.add_all([control, candidate])
    session.flush()
    session.add_all(
        [
            PaperAbOutcome(
                decision_id=control.id,
                evidence_status="EVALUATED",
                net_r=Decimal(control_r),
                exit_reference=Decimal("101"),
                outcome_at=AT,
                reason_code=None,
            ),
            PaperAbOutcome(
                decision_id=candidate.id,
                evidence_status="EVALUATED",
                net_r=Decimal(candidate_r),
                exit_reference=Decimal("102"),
                outcome_at=AT,
                reason_code=None,
            ),
        ]
    )
    session.flush()


def _candidate(body: dict, version: str) -> dict:
    return next(row for row in body["competition"]["candidates"] if row["version"] == version)


def test_exact_n40_is_venue_isolated_and_exposes_remaining_progress(client, session) -> None:
    _closed_pair(
        session,
        pair_seed="1",
        control_key="2",
        candidate_key="3",
        candidate_version="momentum_v2",
        control_venue="CRYPTO",
        candidate_venue="CRYPTO",
    )
    # Same pair shape but the candidate belongs to FORTS. It must not inflate BYBIT N.
    _closed_pair(
        session,
        pair_seed="4",
        control_key="5",
        candidate_key="6",
        candidate_version="mean_reversion_v1",
        control_venue="CRYPTO",
        candidate_venue="MOEX",
    )

    response = client.get(
        "/api/v1/control/dashboard?venue=BYBIT&window_hours=168",
        headers=DEVICE_HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    momentum = _candidate(body, "momentum_v2")
    assert momentum["paper"]["comparable_pairs"] == 1
    assert momentum["paper"]["required_pairs"] == 40
    assert momentum["paper"]["remaining_pairs"] == 39
    assert momentum["paper"]["sample_adequate"] is False
    assert momentum["verdict"] == "WAITING_FOR_SAMPLE"

    cross_venue = _candidate(body, "mean_reversion_v1")
    assert cross_venue["paper"]["comparable_pairs"] == 0
    assert cross_venue["paper"]["required_pairs"] == 40
    assert cross_venue["paper"]["remaining_pairs"] == 40
    assert cross_venue["paper"]["sample_adequate"] is False
    assert cross_venue["verdict"] != "CANDIDATE_WINNING"


def test_n40_progress_is_independent_between_bybit_and_forts(client, session) -> None:
    _closed_pair(
        session,
        pair_seed="7",
        control_key="8",
        candidate_key="9",
        candidate_version="momentum_v2",
        control_venue="CRYPTO",
        candidate_venue="CRYPTO",
    )
    _closed_pair(
        session,
        pair_seed="a",
        control_key="b",
        candidate_key="c",
        candidate_version="momentum_v2",
        control_venue="MOEX",
        candidate_venue="MOEX",
    )
    _closed_pair(
        session,
        pair_seed="d",
        control_key="e",
        candidate_key="f",
        candidate_version="momentum_v2",
        control_venue="MOEX",
        candidate_venue="MOEX",
    )

    bybit = client.get(
        "/api/v1/control/dashboard?venue=BYBIT&window_hours=168",
        headers=DEVICE_HEADERS,
    ).json()
    forts = client.get(
        "/api/v1/control/dashboard?venue=FORTS&window_hours=168",
        headers=DEVICE_HEADERS,
    ).json()

    assert _candidate(bybit, "momentum_v2")["paper"]["comparable_pairs"] == 1
    assert _candidate(forts, "momentum_v2")["paper"]["comparable_pairs"] == 2
    assert _candidate(bybit, "momentum_v2")["paper"]["remaining_pairs"] == 39
    assert _candidate(forts, "momentum_v2")["paper"]["remaining_pairs"] == 38
