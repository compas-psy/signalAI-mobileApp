from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.enums import AssetClass
from app.portfolio import fundamentals as fund
from app.portfolio.research_evidence import PortfolioResearchEvidence

NOW = datetime(2026, 8, 16, 1, 0, tzinfo=UTC)


def _instrument(instrument_id: str, asset_class: AssetClass):
    return SimpleNamespace(
        instrument_id=instrument_id,
        asset_class=asset_class,
        title=instrument_id,
        symbol=instrument_id.split(":")[-1],
        metadata_json={},
    )


def _metrics(value: float):
    return [fund.Metric("quality", "Quality", fund.Measure.MEASURED, value=value)]


def test_screen_applies_research_only_to_equities(monkeypatch, session):
    equity = _instrument("MOEX:EQ:AAA", AssetClass.EQUITY)
    bond = _instrument("MOEX:BOND:BBB", AssetClass.OFZ)

    monkeypatch.setattr(fund, "_equity_metrics", lambda *args, **kwargs: _metrics(0.50))
    monkeypatch.setattr(fund, "_bond_metrics", lambda *args, **kwargs: _metrics(0.50))
    monkeypatch.setattr(fund, "_history_days", lambda *args, **kwargs: 500)
    monkeypatch.setattr(fund, "_median_turnover", lambda *args, **kwargs: 100_000_000)

    seen = {}

    def fake_evidence(_session, instrument_ids, *, as_of):
        seen["ids"] = list(instrument_ids)
        seen["as_of"] = as_of
        return {
            equity.instrument_id: PortfolioResearchEvidence(
                instrument_id=equity.instrument_id,
                research_adjustment=fund.Decimal("0.20"),
                signed_conviction=fund.Decimal("1.00"),
                hypotheses=[{"fingerprint": "mature"}],
            )
        }

    monkeypatch.setattr(fund, "evidence_for", fake_evidence)

    cards = fund.screen(session, [equity, bond], as_of=NOW)
    by_id = {card.instrument_id: card for card in cards}

    # liquidity is a second measured metric with score 1.0, so base = .75
    assert by_id[equity.instrument_id].fundamental_score == 0.75
    assert by_id[equity.instrument_id].score == 0.95
    assert by_id[equity.instrument_id].evidence_json["hypotheses"] == [
        {"fingerprint": "mature"}
    ]
    assert by_id[bond.instrument_id].score == by_id[bond.instrument_id].fundamental_score
    assert seen == {"ids": [equity.instrument_id], "as_of": NOW}


def test_screen_without_research_preserves_existing_equity_score(monkeypatch, session):
    equity = _instrument("MOEX:EQ:AAA", AssetClass.EQUITY)
    monkeypatch.setattr(fund, "_equity_metrics", lambda *args, **kwargs: _metrics(0.50))
    monkeypatch.setattr(fund, "_history_days", lambda *args, **kwargs: 500)
    monkeypatch.setattr(fund, "_median_turnover", lambda *args, **kwargs: 100_000_000)
    monkeypatch.setattr(
        fund,
        "evidence_for",
        lambda *_args, **_kwargs: {
            equity.instrument_id: PortfolioResearchEvidence(instrument_id=equity.instrument_id)
        },
    )

    card = fund.screen(session, [equity], as_of=NOW)[0]

    assert card.score == card.fundamental_score
    assert card.evidence_json["research_adjustment"] == 0.0
    assert card.evidence_json["hypotheses"] == []
