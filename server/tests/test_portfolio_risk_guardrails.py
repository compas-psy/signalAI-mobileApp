from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.models import Bar, Instrument
from app.models.enums import AssetClass, Timeframe, Venue
from app.portfolio import fundamentals as fund
from app.portfolio.research_evidence import PortfolioResearchEvidence

NOW = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)
D = Decimal


def _instrument(session, instrument_id: str = "MOEX:EQ:AAA") -> Instrument:
    row = Instrument(
        instrument_id=instrument_id,
        venue=Venue.MOEX,
        asset_class=AssetClass.EQUITY,
        symbol=instrument_id.split(":")[-1],
        title="AAA",
        currency="RUB",
        tick_size=D("0.01"),
        tick_value=D("0.01"),
        lot_size=1,
        quantity_step=D("1"),
        min_quantity=D("1"),
        contract_multiplier=D("1"),
        metadata_json={},
    )
    session.add(row)
    session.flush()
    return row


def _bar(session, instrument_id: str, when: datetime, *, close: str, turnover: str):
    session.add(
        Bar(
            instrument_id=instrument_id,
            timeframe=Timeframe.D1,
            open_time=when,
            open=D(close),
            high=D(close),
            low=D(close),
            close=D(close),
            volume_units=D("1"),
            volume_notional=D(turnover),
            is_closed=True,
            source="test",
            quality_flags=[],
        )
    )
    session.flush()


def test_market_helpers_are_strictly_as_of_safe(session):
    instrument = _instrument(session)
    _bar(
        session,
        instrument.instrument_id,
        NOW - timedelta(days=30),
        close="90",
        turnover="10000000",
    )
    _bar(
        session,
        instrument.instrument_id,
        NOW - timedelta(days=1),
        close="100",
        turnover="20000000",
    )
    _bar(
        session,
        instrument.instrument_id,
        NOW + timedelta(days=1),
        close="999",
        turnover="999000000",
    )

    assert fund._last_close(session, instrument.instrument_id, as_of=NOW) == D("100")
    assert fund._last_close_at(session, instrument.instrument_id, as_of=NOW) == NOW - timedelta(days=1)
    assert fund._history_days(session, instrument.instrument_id, as_of=NOW) == 30
    assert fund._median_turnover(session, instrument.instrument_id, as_of=NOW, days=90) == 15_000_000


def test_stale_d1_price_rejects_candidate_explicitly(monkeypatch, session):
    instrument = SimpleNamespace(
        instrument_id="MOEX:EQ:AAA",
        asset_class=AssetClass.EQUITY,
        title="AAA",
        symbol="AAA",
        metadata_json={},
    )
    monkeypatch.setattr(fund, "_equity_metrics", lambda *args, **kwargs: [])
    monkeypatch.setattr(fund, "_history_days", lambda *args, **kwargs: 500)
    monkeypatch.setattr(fund, "_median_turnover", lambda *args, **kwargs: 100_000_000)
    monkeypatch.setattr(
        fund,
        "_last_close_at",
        lambda *args, **kwargs: NOW - timedelta(days=8),
    )
    monkeypatch.setattr(
        fund,
        "evidence_for",
        lambda *_args, **_kwargs: {
            instrument.instrument_id: PortfolioResearchEvidence(
                instrument_id=instrument.instrument_id
            )
        },
    )

    card = fund.screen(
        session,
        [instrument],
        as_of=NOW,
        max_price_age_days=7,
    )[0]

    assert "устар" in card.rejected.lower()
    assert "8" in card.rejected
    assert "7" in card.rejected


def test_strong_mature_negative_research_blocks_equity(monkeypatch, session):
    instrument = SimpleNamespace(
        instrument_id="MOEX:EQ:AAA",
        asset_class=AssetClass.EQUITY,
        title="AAA",
        symbol="AAA",
        metadata_json={},
    )
    monkeypatch.setattr(
        fund,
        "_equity_metrics",
        lambda *args, **kwargs: [
            fund.Metric("quality", "Quality", fund.Measure.MEASURED, value=0.90)
        ],
    )
    monkeypatch.setattr(fund, "_history_days", lambda *args, **kwargs: 500)
    monkeypatch.setattr(fund, "_median_turnover", lambda *args, **kwargs: 100_000_000)
    monkeypatch.setattr(fund, "_last_close_at", lambda *args, **kwargs: NOW)
    monkeypatch.setattr(
        fund,
        "evidence_for",
        lambda *_args, **_kwargs: {
            instrument.instrument_id: PortfolioResearchEvidence(
                instrument_id=instrument.instrument_id,
                signed_conviction=D("-0.750000"),
                research_adjustment=D("-0.150000"),
                hypotheses=[{"fingerprint": "strong-negative"}],
            )
        },
    )

    card = fund.screen(session, [instrument], as_of=NOW)[0]

    assert card.fundamental_score > 0
    assert "research" in card.rejected.lower()
    assert "-0.75" in card.rejected


def test_moderate_negative_research_penalises_but_does_not_block(monkeypatch, session):
    instrument = SimpleNamespace(
        instrument_id="MOEX:EQ:AAA",
        asset_class=AssetClass.EQUITY,
        title="AAA",
        symbol="AAA",
        metadata_json={},
    )
    monkeypatch.setattr(
        fund,
        "_equity_metrics",
        lambda *args, **kwargs: [
            fund.Metric("quality", "Quality", fund.Measure.MEASURED, value=0.90)
        ],
    )
    monkeypatch.setattr(fund, "_history_days", lambda *args, **kwargs: 500)
    monkeypatch.setattr(fund, "_median_turnover", lambda *args, **kwargs: 100_000_000)
    monkeypatch.setattr(fund, "_last_close_at", lambda *args, **kwargs: NOW)
    monkeypatch.setattr(
        fund,
        "evidence_for",
        lambda *_args, **_kwargs: {
            instrument.instrument_id: PortfolioResearchEvidence(
                instrument_id=instrument.instrument_id,
                signed_conviction=D("-0.600000"),
                research_adjustment=D("-0.120000"),
                hypotheses=[{"fingerprint": "moderate-negative"}],
            )
        },
    )

    card = fund.screen(session, [instrument], as_of=NOW)[0]

    assert card.rejected == ""
    assert card.score < card.fundamental_score
