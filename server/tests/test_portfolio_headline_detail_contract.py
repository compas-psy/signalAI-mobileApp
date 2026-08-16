from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_db
from app.main import app
from app.models import Instrument, PortfolioModel, PortfolioWeight
from app.models.enums import AssetClass, PackageSize, RiskProfile, Venue
from tests.conftest import DEVICE_HEADERS


def _model(session, *, generated_at, suffix: str, weights: list[tuple[str, str, str]]):
    model = PortfolioModel(
        profile=RiskProfile.OPTIMAL,
        package=PackageSize.BALANCED,
        horizon_years=1,
        expected_return_low=Decimal("0.08"),
        expected_return_high=Decimal("0.14"),
        target_volatility=Decimal("0.12"),
        model_drawdown_limit=Decimal("0.18"),
        cvar_95=Decimal("0.04"),
        rationale="test",
        meets_target=True,
        warnings_json=[],
        stress_json={"год": "-0.18"},
        config_hash=f"test-{suffix}",
        generated_at=generated_at,
        valid_until=generated_at + timedelta(days=30),
    )
    session.add(model)
    session.flush()
    for instrument_id, target_weight, evidence in weights:
        symbol = instrument_id.split(":")[-1]
        existing = session.execute(
            select(Instrument).where(Instrument.instrument_id == instrument_id)
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                Instrument(
                    instrument_id=instrument_id,
                    venue=Venue.MOEX,
                    asset_class=AssetClass.EQUITY,
                    symbol=symbol,
                    title=symbol,
                    is_tradable=True,
                    in_universe=True,
                )
            )
        session.add(
            PortfolioWeight(
                model_id=model.id,
                instrument_id=instrument_id,
                asset_class=AssetClass.EQUITY,
                target_weight=Decimal(target_weight),
                role="core",
                thesis="test thesis",
                kill_conditions="test kill",
                evidence_json={"summary": evidence, "hypothesis_ids": [f"hyp-{suffix}"]},
            )
        )
    session.flush()
    return model


def test_headline_package_exposes_position_evidence_and_model_diff(session):
    now = datetime.now(UTC)
    _model(
        session,
        generated_at=now - timedelta(days=2),
        suffix="old",
        weights=[
            ("EQ:MOEX:AAA", "0.60", "old AAA"),
            ("EQ:MOEX:BBB", "0.40", "old BBB"),
        ],
    )
    latest = _model(
        session,
        generated_at=now - timedelta(hours=1),
        suffix="new",
        weights=[
            ("EQ:MOEX:AAA", "0.50", "mature research supports AAA"),
            ("EQ:MOEX:CCC", "0.50", "mature research supports CCC"),
        ],
    )
    latest.valid_until = now + timedelta(days=1)
    session.commit()

    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app, headers=DEVICE_HEADERS) as client:
            response = client.get("/api/v1/portfolio/headlines?horizon_years=1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    item = next(p for p in response.json()["portfolios"] if p["profile"] == "OPTIMAL")
    package = item["package"]
    assert package["changes"] == {
        "added": ["EQ:MOEX:CCC"],
        "removed": ["EQ:MOEX:BBB"],
        "weight_changed": ["EQ:MOEX:AAA"],
    }
    aaa = next(p for p in package["positions"] if p["instrument_id"] == "EQ:MOEX:AAA")
    assert aaa["evidence"]["summary"] == "mature research supports AAA"
    assert aaa["evidence"]["hypothesis_ids"] == ["hyp-new"]
