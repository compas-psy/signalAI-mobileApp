from __future__ import annotations

from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models import ResearchHypothesis, ResearchObservation
from app.research import spread_runtime
from app.research.sources import sync_registry


PRODUCT = "rosstat:producer_price:fixture_product:796"
INPUT = "rosstat:producer_price:fixture_input:168"


def _basket(*, issuers: tuple[str, ...] = ("GAZP",)) -> spread_runtime.SpreadBasket:
    return spread_runtime.SpreadBasket(
        basket_id="fixture_margin",
        issuers=issuers,
        legs=(
            spread_runtime.SpreadLegConfig(
                observation_type=PRODUCT,
                unit="OKEI:796",
                coefficient=Decimal("1"),
                rationale="fixture product output coefficient",
                side="product",
            ),
            spread_runtime.SpreadLegConfig(
                observation_type=INPUT,
                unit="OKEI:168",
                coefficient=Decimal("1"),
                rationale="fixture input coefficient",
                side="input",
            ),
        ),
        revenue_coverage=1.0,
        cost_coverage=1.0,
        calibrated=True,
        contract_lag_periods=0,
        hedged=False,
        vertically_integrated=False,
    )


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _add_month(
    session,
    *,
    observation_type: str,
    year: int,
    month: int,
    value: str,
    unit: str,
    tradable_at: datetime,
    lineage: str = "rosstat:producer_prices",
) -> ResearchObservation:
    first_seen = tradable_at - timedelta(hours=1)
    row = ResearchObservation(
        observation_type=observation_type,
        entity_id="RU",
        source_id="rosstat",
        period_start=date(year, month, 1),
        period_end=_month_end(year, month),
        published_at=None,
        first_seen_at=first_seen,
        tradable_at=tradable_at,
        publication_time_uncertain=True,
        lineage_root_id=lineage,
        source_locator={"fixture": True},
        raw_sha256="a" * 64,
        value_numeric=Decimal(value),
        value_text="",
        unit=unit,
    )
    session.add(row)
    return row


def _add_complete_month(
    session,
    *,
    year: int,
    month: int,
    product: str,
    input_: str,
    tradable_at: datetime,
) -> None:
    _add_month(
        session,
        observation_type=PRODUCT,
        year=year,
        month=month,
        value=product,
        unit="OKEI:796",
        tradable_at=tradable_at,
    )
    _add_month(
        session,
        observation_type=INPUT,
        year=year,
        month=month,
        value=input_,
        unit="OKEI:168",
        tradable_at=tradable_at,
    )


def test_prepare_periods_averages_three_visible_months_into_one_quarter(session):
    sync_registry(session)
    cutoff = datetime(2026, 4, 10, 12, tzinfo=UTC)
    for month, product, input_ in (
        (1, "100", "40"),
        (2, "110", "50"),
        (3, "120", "60"),
    ):
        _add_complete_month(
            session,
            year=2026,
            month=month,
            product=product,
            input_=input_,
            tradable_at=cutoff - timedelta(days=1),
        )
    session.flush()

    prepared = spread_runtime.prepare_periods(session, _basket(), as_of=cutoff)

    assert [period.period for period in prepared.periods] == ["2026-Q1"]
    period = prepared.periods[0]
    assert period.products[0].price == Decimal("110")
    assert period.inputs[0].price == Decimal("50")
    assert period.unit_margin == Decimal("60")
    assert len(prepared.observation_ids) == 6


def test_prepare_periods_never_uses_not_yet_tradable_month(session):
    sync_registry(session)
    cutoff = datetime(2026, 4, 10, 12, tzinfo=UTC)
    for month in (1, 2):
        _add_complete_month(
            session,
            year=2026,
            month=month,
            product="100",
            input_="50",
            tradable_at=cutoff - timedelta(days=1),
        )
    _add_complete_month(
        session,
        year=2026,
        month=3,
        product="100",
        input_="50",
        tradable_at=cutoff + timedelta(seconds=1),
    )
    session.flush()

    prepared = spread_runtime.prepare_periods(session, _basket(), as_of=cutoff)

    assert prepared.periods == []
    assert "spread_observation_not_yet_tradable" in prepared.reason_codes
    assert "spread_incomplete_quarter" in prepared.reason_codes


def test_prepare_periods_fails_closed_on_unit_mismatch(session):
    sync_registry(session)
    cutoff = datetime(2026, 4, 10, 12, tzinfo=UTC)
    for month in (1, 2, 3):
        _add_complete_month(
            session,
            year=2026,
            month=month,
            product="100",
            input_="50",
            tradable_at=cutoff - timedelta(days=1),
        )
    bad = session.execute(
        select(ResearchObservation).where(
            ResearchObservation.observation_type == PRODUCT,
            ResearchObservation.period_end == _month_end(2026, 3),
        )
    ).scalar_one()
    bad.unit = "OKEI:999"
    session.flush()

    prepared = spread_runtime.prepare_periods(session, _basket(), as_of=cutoff)

    assert prepared.periods == []
    assert "spread_unit_mismatch" in prepared.reason_codes
    assert "spread_incomplete_quarter" in prepared.reason_codes


def test_prepare_periods_drops_whole_quarter_when_one_required_month_is_missing(session):
    sync_registry(session)
    cutoff = datetime(2026, 4, 10, 12, tzinfo=UTC)
    for month in (1, 2, 3):
        _add_month(
            session,
            observation_type=PRODUCT,
            year=2026,
            month=month,
            value="100",
            unit="OKEI:796",
            tradable_at=cutoff - timedelta(days=1),
        )
    for month in (1, 3):
        _add_month(
            session,
            observation_type=INPUT,
            year=2026,
            month=month,
            value="50",
            unit="OKEI:168",
            tradable_at=cutoff - timedelta(days=1),
        )
    session.flush()

    prepared = spread_runtime.prepare_periods(session, _basket(), as_of=cutoff)

    assert prepared.periods == []
    assert "spread_incomplete_quarter" in prepared.reason_codes


def test_empty_production_registry_is_explicit_no_signal(session):
    now = datetime(2026, 8, 15, 8, tzinfo=UTC)

    report = spread_runtime.run_spread(session, baskets=(), now=now)

    assert report.signals == 0
    assert report.hypotheses == 0
    assert "SPREAD: production baskets not configured" in report.skipped


def test_fixture_basket_reaches_common_pipeline_only_with_twelve_complete_quarters(session):
    sync_registry(session)
    now = datetime(2026, 1, 15, 12, tzinfo=UTC)
    month_index = 0
    for year in (2023, 2024, 2025):
        for month in range(1, 13):
            _add_complete_month(
                session,
                year=year,
                month=month,
                product=str(100 + month_index * 3),
                input_=str(60 + month_index),
                tradable_at=now - timedelta(days=1),
            )
            month_index += 1
    session.flush()

    report = spread_runtime.run_spread(session, baskets=(_basket(),), now=now)
    session.flush()

    assert report.complete_quarters == 12
    assert report.signals == 1
    assert report.hypotheses == 1
    rows = list(session.execute(select(ResearchHypothesis)).scalars())
    assert len(rows) == 1
    assert rows[0].entity_id == "GAZP"
    assert rows[0].instrument_id == "MOEX:EQ:GAZP"


def test_too_few_complete_quarters_remains_no_signal(session):
    sync_registry(session)
    now = datetime(2026, 1, 15, 12, tzinfo=UTC)
    month_index = 0
    for year, months in ((2023, range(4, 13)), (2024, range(1, 13)), (2025, range(1, 13))):
        for month in months:
            _add_complete_month(
                session,
                year=year,
                month=month,
                product=str(100 + month_index * 3),
                input_=str(60 + month_index),
                tradable_at=now - timedelta(days=1),
            )
            month_index += 1
    session.flush()

    report = spread_runtime.run_spread(session, baskets=(_basket(),), now=now)

    assert report.complete_quarters == 11
    assert report.signals == 0
    assert report.hypotheses == 0
    assert any("spread_uncalibrated_proxy" in item for item in report.skipped)
