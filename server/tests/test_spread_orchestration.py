from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.api.v1 import research as research_api
from app.main import app
from app.models import ResearchObservation
from app.research import run_engines
from app.research.sources import sync_registry


class _Summary:
    def __init__(self, text: str) -> None:
        self.text = text

    def summary(self) -> str:
        return self.text


def _rosstat_observation() -> ResearchObservation:
    first_seen = datetime(2026, 7, 24, 12, tzinfo=UTC)
    return ResearchObservation(
        observation_type="rosstat:producer_price:24_10_3:168",
        entity_id="RU",
        source_id="rosstat",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 30),
        published_at=None,
        first_seen_at=first_seen,
        tradable_at=first_seen + timedelta(hours=1),
        publication_time_uncertain=True,
        lineage_root_id="rosstat:producer_prices",
        source_locator={"okpd2": "24.10.3", "okei": "168"},
        raw_sha256="a" * 64,
        value_numeric=Decimal("64250.10"),
        value_text="",
        unit="OKEI:168",
    )


def test_explicit_research_run_surfaces_unconfigured_spread(session):
    report = run_engines.run_demand(
        session,
        include_hiring=False,
        include_spread=True,
        now=datetime(2026, 8, 15, 10, tzinfo=UTC),
    )

    assert any(
        "SPREAD: production baskets not configured" in item
        for item in report.skipped
    )
    assert report.signals == 0
    assert report.hypotheses == 0


def test_scheduler_mode_invokes_spread_without_enabling_hiring(
    session, monkeypatch
):
    called: list[datetime] = []

    def fake_spread(_session, *, now=None, baskets=None):
        assert baskets is None
        called.append(now)
        return type(
            "SpreadReport",
            (),
            {"signals": 0, "hypotheses": 0, "skipped": ["spread-called"]},
        )()

    monkeypatch.setattr(run_engines, "_running_as_scheduler", lambda: True)
    monkeypatch.setattr(run_engines, "run_spread", fake_spread)
    moment = datetime(2026, 8, 15, 10, tzinfo=UTC)

    report = run_engines.run_demand(
        session,
        include_hiring=False,
        now=moment,
    )

    assert called == [moment]
    assert "spread-called" in report.skipped


def test_spread_engine_status_is_wired_and_names_configuration_blocker(session):
    sync_registry(session)
    session.add(_rosstat_observation())
    session.flush()

    engines = research_api._engines(session)
    spread = next(item for item in engines if item.key == "SPREAD")

    assert spread.wired is True
    assert spread.observations == 1
    assert spread.unique_periods == 1
    assert spread.history_ready is False
    assert "production baskets not configured" in spread.history_note


def test_manual_research_refresh_runs_collection_and_all_live_engines(
    session, monkeypatch
):
    calls: dict[str, object] = {}

    def fake_collect(_session, *, now=None):
        calls["collect_now"] = now
        return _Summary("collection-ok")

    def fake_run(
        _session,
        *,
        now=None,
        include_hiring=None,
        include_spread=None,
    ):
        calls["engine_now"] = now
        calls["include_hiring"] = include_hiring
        calls["include_spread"] = include_spread
        return _Summary("engines-ok")

    def fake_expire(_session, *, now=None):
        calls["expire_now"] = now
        return 2

    monkeypatch.setattr(research_api, "collect_all", fake_collect)
    monkeypatch.setattr(research_api, "run_demand", fake_run)
    monkeypatch.setattr(research_api, "expire_hypotheses", fake_expire)

    result = research_api.refresh_research(session=session)

    assert result.collected == "collection-ok"
    assert result.engines == "engines-ok"
    assert result.expired_hypotheses == 2
    assert calls["include_hiring"] is True
    assert calls["include_spread"] is True
    assert calls["collect_now"] == calls["engine_now"] == calls["expire_now"]
    assert "/api/v1/research/refresh" in app.openapi()["paths"]
