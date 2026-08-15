"""Fail-closed model selection for portfolio rebalance.

A rebalance is meaningful only relative to the package the owner selected.
When several conservative/optimal/aggressive models coexist, silently using
whichever model happened to be generated last can produce the opposite trade.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import app
from app.models import Account, Holding, PortfolioModel, PortfolioWeight
from app.models.enums import AssetClass, PackageSize, RiskProfile
from tests.conftest import DEVICE_HEADERS

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _model(
    session: Session,
    *,
    profile: RiskProfile,
    generated_at: datetime,
    weights: dict[str, str],
) -> PortfolioModel:
    model = PortfolioModel(
        profile=profile,
        package=PackageSize.SIMPLE,
        horizon_years=1,
        expected_return_low=Decimal("0.08"),
        expected_return_high=Decimal("0.12"),
        target_volatility=Decimal("0.10"),
        model_drawdown_limit=Decimal("0.15"),
        rationale="selection test",
        generated_at=generated_at,
        valid_until=generated_at + timedelta(days=30),
        config_hash="0" * 64,
    )
    session.add(model)
    session.flush()
    for instrument_id, target in weights.items():
        session.add(
            PortfolioWeight(
                model_id=model.id,
                instrument_id=instrument_id,
                asset_class=AssetClass.EQUITY,
                target_weight=Decimal(target),
                role="рост капитала",
                thesis="test",
                kill_conditions="test",
            )
        )
    session.flush()
    session.refresh(model)
    return model


def _account_with_holdings(session: Session) -> Account:
    account = Account(
        external_id="ACC-MODEL-SELECTION",
        broker="tinvest",
        title="Инвестиции",
        circuit="investment",
    )
    session.add(account)
    session.flush()
    session.add_all(
        [
            Holding(
                account_id=account.id,
                instrument_id="A",
                as_of=NOW.date(),
                quantity=Decimal("1"),
                market_value=Decimal("80000"),
                asset_class=AssetClass.EQUITY,
            ),
            Holding(
                account_id=account.id,
                instrument_id="B",
                as_of=NOW.date(),
                quantity=Decimal("1"),
                market_value=Decimal("20000"),
                asset_class=AssetClass.EQUITY,
            ),
        ]
    )
    session.flush()
    return account


def _get(session: Session, path: str) -> dict:
    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app, headers=DEVICE_HEADERS) as client:
            response = client.get(path)
            assert response.status_code == 200
            return response.json()
    finally:
        app.dependency_overrides.clear()


def test_rebalance_uses_explicit_selected_model_even_when_newer_model_exists(session: Session):
    selected = _model(
        session,
        profile=RiskProfile.CONSERVATIVE,
        generated_at=NOW,
        weights={"A": "0.5", "B": "0.5"},
    )
    newer = _model(
        session,
        profile=RiskProfile.AGGRESSIVE,
        generated_at=NOW + timedelta(minutes=1),
        weights={"A": "1.0"},
    )
    assert newer.id != selected.id
    _account_with_holdings(session)

    body = _get(session, f"/api/v1/portfolio/rebalance?model_id={selected.id}")

    assert body["model_id"] == str(selected.id)
    actions = {item["instrument_id"]: item["side"] for item in body["actions"]}
    assert actions == {"A": "SELL", "B": "BUY"}


def test_rebalance_fails_closed_when_multiple_models_exist_without_selection(session: Session):
    _model(
        session,
        profile=RiskProfile.CONSERVATIVE,
        generated_at=NOW,
        weights={"A": "0.5", "B": "0.5"},
    )
    _model(
        session,
        profile=RiskProfile.AGGRESSIVE,
        generated_at=NOW + timedelta(minutes=1),
        weights={"A": "1.0"},
    )
    _account_with_holdings(session)

    body = _get(session, "/api/v1/portfolio/rebalance")

    assert body["needed"] is False
    assert body["model_id"] == ""
    assert body["actions"] == []
    assert "выберите пакет" in body["reason"].lower()


def test_rebalance_fails_closed_for_unknown_model_id(session: Session):
    _model(
        session,
        profile=RiskProfile.CONSERVATIVE,
        generated_at=NOW,
        weights={"A": "0.5", "B": "0.5"},
    )
    _account_with_holdings(session)

    body = _get(
        session,
        "/api/v1/portfolio/rebalance?model_id=00000000-0000-0000-0000-000000000001",
    )

    assert body["needed"] is False
    assert body["model_id"] == ""
    assert body["actions"] == []
    assert "пакет не найден" in body["reason"].lower()
