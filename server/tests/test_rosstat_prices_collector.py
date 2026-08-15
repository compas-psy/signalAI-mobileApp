from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import ResearchObservation
from app.research import collector
from app.research.adapters import rosstat_prices
from app.research.collect import Fetched
from app.research.sources import sync_registry


CATALOGUE = f'''<html><body><a href="/storage/producer_prices.xlsx">{rosstat_prices.DATASET_TITLE}</a></body></html>'''
WORKBOOK_URL = "https://rosstat.gov.ru/storage/producer_prices.xlsx"


def _point() -> rosstat_prices.ProducerPricePoint:
    return rosstat_prices.ProducerPricePoint(
        product=rosstat_prices.ProductIdentity.create(
            okpd2="19.20.21.100",
            okei="168",
            name="Бензин автомобильный",
        ),
        period=date(2026, 3, 1),
        value=Decimal("34925"),
    )


def _fetched(moment: datetime) -> Fetched:
    return Fetched(
        url=WORKBOOK_URL,
        status=200,
        body=b"xlsx-vintage",
        requested_at=moment,
        responded_at=moment,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _install_source(monkeypatch, moment: datetime, *, parser=None) -> None:
    monkeypatch.setattr(collector, "_text", lambda url: CATALOGUE)
    monkeypatch.setattr(collector, "_get", lambda url, now: _fetched(moment))
    monkeypatch.setattr(
        rosstat_prices,
        "parse_workbook",
        parser or (lambda content: [_point()]),
    )


def test_collect_rosstat_prices_persists_conservative_observation(session, monkeypatch):
    sync_registry(session)
    moment = datetime(2026, 8, 13, 10, tzinfo=UTC)
    _install_source(monkeypatch, moment)

    report = collector.collect_rosstat_prices(session, now=moment)
    session.flush()

    assert report.fetched >= 1
    assert report.written == 1
    row = session.execute(select(ResearchObservation)).scalar_one()
    point = _point()
    assert row.source_id == "rosstat"
    assert row.entity_id == "RU"
    assert row.observation_type == rosstat_prices.observation_type(point.product)
    assert row.value_numeric == Decimal("34925")
    assert row.unit == "OKEI:168"
    assert row.period_start == date(2026, 3, 1)
    assert row.period_end == date(2026, 3, 31)
    assert row.published_at is None
    assert row.first_seen_at == moment
    assert row.publication_time_uncertain is True
    assert row.tradable_at > moment
    assert row.lineage_root_id == "rosstat:producer_prices"
    assert row.raw_sha256 == _fetched(moment).sha256
    assert row.source_locator == {
        "catalogue": rosstat_prices.CATALOG_URL,
        "dataset": rosstat_prices.DATASET_TITLE,
        "url": WORKBOOK_URL,
        "okpd2": "19.20.21.100",
        "okei": "168",
        "product_name": "Бензин автомобильный",
    }


def test_repeat_collection_keeps_original_first_seen_and_tradable_at(session, monkeypatch):
    sync_registry(session)
    first = datetime(2026, 8, 13, 10, tzinfo=UTC)
    _install_source(monkeypatch, first)
    initial_report = collector.collect_rosstat_prices(session, now=first)
    session.flush()
    assert initial_report.written == 1
    original = session.execute(select(ResearchObservation)).scalar_one()
    original_seen = original.first_seen_at
    original_tradable = original.tradable_at

    later = datetime(2026, 8, 20, 10, tzinfo=UTC)
    _install_source(monkeypatch, later)
    repeated_report = collector.collect_rosstat_prices(session, now=later)
    session.flush()

    rows = list(session.execute(select(ResearchObservation)).scalars())
    assert len(rows) == 1
    assert repeated_report.written == 0
    assert repeated_report.duplicates == 1
    assert rows[0].first_seen_at == original_seen
    assert rows[0].tradable_at == original_tradable


def test_schema_failure_is_explicit_and_writes_nothing(session, monkeypatch):
    sync_registry(session)
    moment = datetime(2026, 8, 13, 10, tzinfo=UTC)

    def broken(_: bytes):
        raise rosstat_prices.WorkbookSchemaError("ОКПД2 column moved")

    _install_source(monkeypatch, moment, parser=broken)
    report = collector.collect_rosstat_prices(session, now=moment)
    session.flush()

    assert report.written == 0
    assert report.errors
    assert "ОКПД2" in report.errors[0]
    assert session.execute(select(ResearchObservation)).scalars().all() == []


def test_collect_all_includes_rosstat(session, monkeypatch):
    calls: list[str] = []

    def report(name: str) -> collector.CollectReport:
        calls.append(name)
        return collector.CollectReport(attempted=1, fetched=1, written=1)

    monkeypatch.setattr(collector, "collect_cbr", lambda session, now=None: report("cbr"))
    monkeypatch.setattr(collector, "collect_fns", lambda session, now=None: report("fns"))
    monkeypatch.setattr(
        collector,
        "collect_rosstat_prices",
        lambda session, now=None: report("rosstat"),
        raising=False,
    )

    total = collector.collect_all(session, now=datetime(2026, 8, 13, tzinfo=UTC))

    assert calls == ["cbr", "fns", "rosstat"]
    assert total.attempted == 3
    assert total.fetched == 3
    assert total.written == 3
