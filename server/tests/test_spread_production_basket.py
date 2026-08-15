from __future__ import annotations

from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.api.v1 import research as research_api
from app.models import ResearchObservation
from app.research.engines import spread
from app.research.issuers import automatic, of
from app.research.sources import sync_registry
from app.research.spread_runtime import PRODUCTION_BASKETS


def _rual_basket():
    return next(
        basket
        for basket in PRODUCTION_BASKETS
        if basket.basket_id == "rual_primary_aluminium_alumina"
    )


def test_rual_is_a_strongly_identified_automatic_issuer():
    issuer = of("RUAL")

    assert issuer is not None
    assert issuer.name == "РУСАЛ"
    assert issuer.section == "C"
    assert issuer.inn == "3906394938"
    assert automatic(issuer) is True


def test_rual_production_basket_uses_exact_live_rosstat_series_and_fixed_mass_balance():
    basket = _rual_basket()

    assert basket.issuers == ("RUAL",)
    assert basket.calibrated is False
    assert basket.contract_lag_periods == 0
    assert basket.hedged is False
    assert basket.vertically_integrated is True
    assert basket.revenue_coverage == 0.80
    assert basket.cost_coverage == 0.187

    product = next(leg for leg in basket.legs if leg.side == "product")
    alumina = next(leg for leg in basket.legs if leg.side == "input")

    assert product.observation_type == "rosstat:producer_price:24_42_11:168"
    assert product.unit == "OKEI:168"
    assert product.coefficient == Decimal("1")
    assert "Rosstat" in product.rationale
    assert "24.42.11" in product.rationale

    assert alumina.observation_type == "rosstat:producer_price:24_42_12:168"
    assert alumina.unit == "OKEI:168"
    assert alumina.coefficient == Decimal("2.0")
    assert "International Aluminium Institute" in alumina.rationale
    assert "2" in alumina.rationale


def _period(index: int) -> spread.Period:
    year = 2023 + index // 4
    quarter = index % 4 + 1
    return spread.Period(
        period=f"{year}-Q{quarter}",
        products=(
            spread.Leg(
                metric="unwrought_aluminium",
                price=Decimal(100 + index * 5),
                coefficient=Decimal("1"),
            ),
        ),
        inputs=(
            spread.Leg(
                metric="alumina",
                price=Decimal(20 + index),
                coefficient=Decimal("2"),
            ),
        ),
    )


def test_vertical_integration_has_an_explicit_confidence_cap():
    result = spread.evaluate(
        [_period(index) for index in range(12)],
        revenue_coverage=1.0,
        cost_coverage=1.0,
        calibrated=True,
        vertically_integrated=True,
    )

    assert spread.VERTICAL_INTEGRATION_CAP == Decimal("0.50")
    assert "spread_vertical_integration" in result.reason_codes
    assert result.confidence <= spread.VERTICAL_INTEGRATION_CAP


def test_spread_owner_status_does_not_mistake_months_for_complete_quarters(session):
    sync_registry(session)
    first_seen = datetime(2026, 8, 1, 12, tzinfo=UTC)
    for month in range(1, 7):
        session.add(
            ResearchObservation(
                observation_type="rosstat:producer_price:24_42_11:168",
                entity_id="RU",
                source_id="rosstat",
                period_start=date(2026, month, 1),
                period_end=date(2026, month, monthrange(2026, month)[1]),
                published_at=None,
                first_seen_at=first_seen,
                tradable_at=first_seen + timedelta(hours=1),
                publication_time_uncertain=True,
                lineage_root_id="rosstat:producer_prices",
                source_locator={"okpd2": "24.42.11", "okei": "168"},
                raw_sha256=f"{month:064x}",
                value_numeric=Decimal(1000 + month),
                value_text="",
                unit="OKEI:168",
            )
        )
    session.flush()

    state = next(item for item in research_api._engines(session) if item.key == "SPREAD")

    assert state.wired is True
    assert state.observations == 6
    assert state.unique_periods == 0
    assert state.history_ready is False
    assert "complete quarters" in state.history_note
    assert "rual_primary_aluminium_alumina" in state.history_note
