from types import SimpleNamespace

from app.investment_radar import _label, _liquidity, _research, _technical


def test_daily_radar_technical_score_rewards_constructive_d1_context():
    rising = [100 + i for i in range(70)]
    falling = [170 - i for i in range(70)]

    up, up20, up60, _ = _technical(rising)
    down, down20, down60, _ = _technical(falling)

    assert up > down
    assert up20 > 0 and up60 > 0
    assert down20 < 0 and down60 < 0
    assert 0 <= up <= 100
    assert 0 <= down <= 100


def test_daily_radar_keeps_missing_research_as_explicit_baseline():
    score, thesis, reasons, has_research = _research(None)

    assert score == 35
    assert not has_research
    assert "не сформирован" in thesis
    assert reasons


def test_daily_radar_negative_research_does_not_rank_like_positive():
    common = dict(
        evidence_score=0.9,
        economic_score=0.9,
        market_context_score=0.8,
        title="сильное изменение",
    )
    positive = SimpleNamespace(direction="positive", **common)
    negative = SimpleNamespace(direction="negative", **common)

    pos, _, _, _ = _research(positive)
    neg, _, _, _ = _research(negative)

    assert pos > neg


def test_daily_radar_liquidity_is_monotonic_and_bounded():
    low, _ = _liquidity([10_000_000.0] * 20)
    high, _ = _liquidity([1_000_000_000.0] * 20)

    assert 0 <= low < high <= 100


def test_daily_radar_labels_cover_weak_tail_instead_of_filtering_it():
    assert _label(80) == "стоит смотреть"
    assert _label(65) == "в фокус"
    assert _label(50) == "нейтрально"
    assert _label(35) == "слабая"
    assert _label(10) == "сейчас мимо"
