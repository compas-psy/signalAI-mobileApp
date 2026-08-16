from __future__ import annotations

import math
from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.enums import AssetClass, HypothesisState, ResearchDirection
from app.portfolio import equity_ranking as ranking
from app.portfolio.fundamentals import Fundamental, Measure, Metric


def _instrument(key: str, symbol: str):
    return SimpleNamespace(
        instrument_id=key,
        asset_class=AssetClass.EQUITY,
        symbol=symbol,
        title=f"Company {symbol}",
    )


def _card(key: str, score: float, *, rejected: str = "") -> Fundamental:
    return Fundamental(
        instrument_id=key,
        asset_class=AssetClass.EQUITY,
        title=key,
        metrics=[
            Metric(
                "test",
                "test",
                Measure.MEASURED,
                value=score,
                text=f"fundamental {score:.0%}",
            )
        ],
        rejected=rejected,
    )


def _uptrend() -> list[float]:
    return [100.0 + i * 0.8 for i in range(240)]


def _downtrend() -> list[float]:
    return [300.0 - i * 0.7 for i in range(240)]


def _pre_breakout() -> tuple[list[float], list[float]]:
    # Noisy constructive trend followed by a quiet 30-session coil near the
    # high. Last five sessions accelerate slightly while turnover expands.
    closes = [100.0 + 0.10 * i + 1.15 * math.sin(i * 0.7) for i in range(90)]
    anchor = closes[-1]
    closes.extend(
        anchor + 0.025 * i + 0.14 * math.sin(i * 0.55)
        for i in range(30)
    )
    for i in range(5):
        closes[-5 + i] += 0.12 * (i + 1)
    turnover = [100.0] * (len(closes) - 5) + [175.0, 180.0, 190.0, 185.0, 195.0]
    return closes, turnover


def test_technical_overlay_ranks_slow_uptrend_above_downtrend():
    up = ranking._technical(_uptrend())
    down = ranking._technical(_downtrend())

    assert up["ready"] is True
    assert down["ready"] is True
    assert up["score"] > down["score"]
    assert up["state"] == "восходящий D1"
    assert down["state"] == "нисходящий D1"


def test_early_radar_prefers_coil_before_breakout_to_already_vertical_spike():
    pre, turnover = _pre_breakout()
    chased = list(pre)
    start = chased[-6]
    chased[-5:] = [start * factor for factor in (1.04, 1.09, 1.15, 1.22, 1.30)]

    early = ranking._technical(pre, turnover)["early"]
    late = ranking._technical(chased, turnover)["early"]

    assert early["score"] > late["score"]
    assert early["turnover_ratio_5v20"] is not None
    assert early["turnover_ratio_5v20"] > 1.5
    assert late["chase_penalty"] >= 0.9
    assert late["state"] == "поздно / не догонять"


def test_daily_ranking_keeps_weak_names_and_sorts_descending(session, monkeypatch):
    strong_id = "MOEX:EQ:GOOD"
    weak_id = "MOEX:EQ:WEAK"
    universe = [_instrument(strong_id, "GOOD"), _instrument(weak_id, "WEAK")]

    monkeypatch.setattr(ranking, "investment_universe", lambda _session: universe)
    monkeypatch.setattr(
        ranking.fund,
        "screen",
        lambda _session, _universe, fetch=None: [
            _card(strong_id, 0.85),
            _card(weak_id, 0.20, rejected="ликвидность ниже порога"),
        ],
    )
    monkeypatch.setattr(
        ranking,
        "_closes",
        lambda _session, key: _uptrend() if key == strong_id else _downtrend(),
    )
    monkeypatch.setattr(ranking, "_latest_hypotheses", lambda _session: {})

    snapshot = ranking.build_daily_ranking(
        session,
        now=datetime(2026, 8, 10, 0, 30, tzinfo=UTC),
    )

    assert snapshot.universe_count == 2
    assert snapshot.scored_count == 2
    assert snapshot.methodology == "equity_rank_v2_early"
    assert [item["symbol"] for item in snapshot.items_json] == ["GOOD", "WEAK"]
    assert snapshot.items_json[0]["rank"] == 1
    assert snapshot.items_json[0]["score"] > snapshot.items_json[1]["score"]
    assert "early_score" in snapshot.items_json[0]
    assert "why_now" in snapshot.items_json[0]
    assert "confirmation" in snapshot.items_json[0]
    assert "invalidation" in snapshot.items_json[0]
    assert snapshot.items_json[1]["eligible"] is False
    assert snapshot.items_json[1]["score"] <= 39
    assert "ликвидность" in snapshot.items_json[1]["warnings"][0]


def test_daily_ranking_is_idempotent_within_moscow_day(session, monkeypatch):
    key = "MOEX:EQ:ONE"
    universe = [_instrument(key, "ONE")]
    calls = {"screen": 0}

    monkeypatch.setattr(ranking, "investment_universe", lambda _session: universe)

    def screen(_session, _universe, fetch=None):
        calls["screen"] += 1
        return [_card(key, 0.7)]

    monkeypatch.setattr(ranking.fund, "screen", screen)
    monkeypatch.setattr(ranking, "_closes", lambda _session, _key: _uptrend())
    monkeypatch.setattr(ranking, "_latest_hypotheses", lambda _session: {})

    first = ranking.build_daily_ranking(
        session,
        now=datetime(2026, 8, 10, 0, 15, tzinfo=UTC),
    )
    second = ranking.build_daily_ranking(
        session,
        now=datetime(2026, 8, 10, 18, 15, tzinfo=UTC),
    )

    assert first.id == second.id
    assert calls["screen"] == 1


def test_positive_early_hypothesis_is_bonus_not_admission_requirement():
    row = SimpleNamespace(
        title="спрос ускоряется",
        direction=ResearchDirection.POSITIVE,
        state=HypothesisState.CONFIRMED,
        evidence_score=0.8,
        economic_score=0.7,
        research_priority=82,
        as_of=datetime(2026, 8, 9, tzinfo=UTC),
    )

    adjustment, payload = ranking._hypothesis_overlay(row)
    neutral_adjustment, neutral_payload = ranking._hypothesis_overlay(None)

    assert adjustment > 0
    assert adjustment <= 10
    assert payload is not None and payload["title"] == "спрос ускоряется"
    assert neutral_adjustment == 0
    assert neutral_payload is None
