from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

from app.models import PortfolioWeight
from app.models.enums import AssetClass, PackageSize, RiskProfile
from app.portfolio.build import Package, Position, _persist

NOW = datetime(2026, 8, 16, 1, 0, tzinfo=UTC)


def test_persisted_position_keeps_research_score_decomposition(session):
    evidence = {
        "fundamental_score": 0.55,
        "signed_conviction": 0.6,
        "research_adjustment": 0.12,
        "combined_score": 0.67,
        "hypotheses": [
            {
                "hypothesis_id": "11111111-1111-1111-1111-111111111111",
                "fingerprint": "hiring-growth",
                "version": 2,
                "state": "confirmed_hypothesis",
                "direction": "positive",
            }
        ],
    }
    package = Package(
        profile=RiskProfile.OPTIMAL,
        package=PackageSize.BALANCED,
        horizon_years=1,
        admitted=True,
        reason="ok",
        positions=[
            Position(
                instrument_id="MOEX:EQ:TEST",
                title="TEST",
                asset_class=AssetClass.EQUITY,
                weight=1.0,
                stability=0.01,
                role="рост капитала",
                thesis="test",
                kill="test",
                score=0.67,
                expected_return=0.10,
                evidence=evidence,
            )
        ],
        expected_low=0.05,
        expected_high=0.15,
        volatility=0.10,
        drawdown=0.12,
        cvar_95=0.02,
    )

    _persist(session, package, cfg=SimpleNamespace(config_hash="a" * 64), now=NOW)
    session.flush()

    row = session.execute(select(PortfolioWeight)).scalars().one()
    assert row.score == Decimal("0.670000000000")
    assert row.evidence_json == evidence
