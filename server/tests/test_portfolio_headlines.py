from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models.enums import PackageSize, RiskProfile
from app.portfolio.headlines import HeadlineStatus, select_headlines
from tests.conftest import DEVICE_HEADERS

NOW = datetime(2026, 8, 16, 2, 0, tzinfo=UTC)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app, headers=DEVICE_HEADERS) as c:
        yield c
    app.dependency_overrides.clear()


def _model(
    profile: RiskProfile,
    package: PackageSize,
    *,
    horizon: int = 1,
    meets_target: bool = True,
    low: float = 0.10,
    cvar: float = 0.03,
    drawdown: float = 0.12,
    generated_minutes_ago: int = 1,
):
    return SimpleNamespace(
        id=f"{profile}:{package}:{horizon}:{generated_minutes_ago}",
        profile=profile,
        package=package,
        horizon_years=horizon,
        meets_target=meets_target,
        expected_return_low=low,
        cvar_95=cvar,
        model_drawdown_limit=drawdown,
        generated_at=NOW - timedelta(minutes=generated_minutes_ago),
        valid_until=NOW + timedelta(days=1),
    )


def test_headlines_always_expose_exactly_three_profile_slots():
    result = select_headlines([], horizon_years=1, as_of=NOW)

    assert [item.profile for item in result] == [
        RiskProfile.CONSERVATIVE,
        RiskProfile.OPTIMAL,
        RiskProfile.AGGRESSIVE,
    ]
    assert [item.label for item in result] == [
        "Консервативный",
        "Сбалансированный",
        "Доходный",
    ]
    assert all(item.status is HeadlineStatus.MISSING for item in result)
    assert all(item.model is None for item in result)
    assert all(item.reason for item in result)


def test_headline_never_borrows_model_from_another_horizon():
    models = [
        _model(RiskProfile.CONSERVATIVE, PackageSize.BALANCED, horizon=5),
        _model(RiskProfile.OPTIMAL, PackageSize.BALANCED, horizon=5),
        _model(RiskProfile.AGGRESSIVE, PackageSize.BALANCED, horizon=5),
    ]

    result = select_headlines(models, horizon_years=1, as_of=NOW)

    assert all(item.status is HeadlineStatus.MISSING for item in result)


def test_target_compliant_balanced_variant_is_preferred_for_headline():
    models = [
        _model(RiskProfile.OPTIMAL, PackageSize.SIMPLE, low=0.14),
        _model(RiskProfile.OPTIMAL, PackageSize.BALANCED, low=0.11),
        _model(RiskProfile.OPTIMAL, PackageSize.MAX_POTENTIAL, low=0.18),
    ]

    item = select_headlines(models, horizon_years=1, as_of=NOW)[1]

    assert item.status is HeadlineStatus.READY
    assert item.model.package is PackageSize.BALANCED


def test_when_balanced_misses_target_best_compliant_candidate_is_selected():
    models = [
        _model(RiskProfile.AGGRESSIVE, PackageSize.BALANCED, meets_target=False, low=0.20),
        _model(RiskProfile.AGGRESSIVE, PackageSize.SIMPLE, low=0.12, cvar=0.04),
        _model(RiskProfile.AGGRESSIVE, PackageSize.MAX_POTENTIAL, low=0.17, cvar=0.06),
    ]

    item = select_headlines(models, horizon_years=1, as_of=NOW)[2]

    assert item.status is HeadlineStatus.READY
    assert item.model.package is PackageSize.MAX_POTENTIAL


def test_if_none_meets_target_return_safest_visible_fallback_not_missing():
    models = [
        _model(
            RiskProfile.CONSERVATIVE,
            PackageSize.SIMPLE,
            meets_target=False,
            cvar=0.05,
            drawdown=0.14,
        ),
        _model(
            RiskProfile.CONSERVATIVE,
            PackageSize.BALANCED,
            meets_target=False,
            cvar=0.03,
            drawdown=0.11,
        ),
        _model(
            RiskProfile.CONSERVATIVE,
            PackageSize.MAX_POTENTIAL,
            meets_target=False,
            cvar=0.08,
            drawdown=0.18,
        ),
    ]

    item = select_headlines(models, horizon_years=1, as_of=NOW)[0]

    assert item.status is HeadlineStatus.RISKIER_THAN_TARGET
    assert item.model.package is PackageSize.BALANCED
    assert "риск" in item.reason.lower()


def test_expired_variants_are_not_exposed_as_current_headlines():
    model = _model(RiskProfile.OPTIMAL, PackageSize.BALANCED)
    model.valid_until = NOW

    item = select_headlines([model], horizon_years=1, as_of=NOW)[1]

    assert item.status is HeadlineStatus.MISSING
    assert item.model is None


def test_latest_version_of_same_internal_variant_wins_before_selection():
    older = _model(
        RiskProfile.OPTIMAL,
        PackageSize.BALANCED,
        low=0.20,
        generated_minutes_ago=20,
    )
    latest = _model(
        RiskProfile.OPTIMAL,
        PackageSize.BALANCED,
        low=0.09,
        generated_minutes_ago=1,
    )

    item = select_headlines([older, latest], horizon_years=1, as_of=NOW)[1]

    assert item.model is latest


def test_headlines_api_exposes_three_explicit_owner_slots(client):
    response = client.get("/api/v1/portfolio/headlines?horizon_years=1")

    assert response.status_code == 200
    body = response.json()
    assert body["horizon_years"] == 1
    assert [item["profile"] for item in body["portfolios"]] == [
        "CONSERVATIVE",
        "OPTIMAL",
        "AGGRESSIVE",
    ]
    assert [item["label"] for item in body["portfolios"]] == [
        "Консервативный",
        "Сбалансированный",
        "Доходный",
    ]
    assert all(item["status"] == "missing" for item in body["portfolios"])
    assert all(item["package"] is None for item in body["portfolios"])
    assert all(item["reason"] for item in body["portfolios"])
