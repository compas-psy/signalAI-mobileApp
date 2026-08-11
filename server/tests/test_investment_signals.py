from app.portfolio.investment_signals import _signal


def _item(**overrides):
    base = {
        "rank": 1,
        "instrument_id": "MOEX:EQ:SBER",
        "symbol": "SBER",
        "title": "Сбербанк",
        "score": 76.0,
        "eligible": True,
        "fundamental_score": 70.0,
        "technical_score": 66.0,
        "catalyst_adjustment": 0.0,
        "technical_state": "выше MA50 · положительный импульс",
        "price": 320.0,
        "fundamental_facts": ["дивиденды за год 10.2%", "капитализация 8 000 млрд ₽"],
        "technical_facts": ["цена выше MA50", "6 мес. +18.0%"],
        "warnings": [],
        "hypothesis": None,
    }
    base.update(overrides)
    return base


def test_strong_measured_mix_becomes_accumulate():
    signal = _signal(_item())
    assert signal is not None
    assert signal.action == "ACCUMULATE"
    assert signal.action_label == "НАБИРАТЬ"
    assert signal.fundamental_score == 70
    assert signal.technical_score == 66


def test_positive_material_research_overlay_becomes_early_signal():
    signal = _signal(
        _item(
            score=64.0,
            fundamental_score=58.0,
            technical_score=54.0,
            catalyst_adjustment=5.5,
            hypothesis={
                "title": "рост подтверждённого спроса",
                "direction": "POSITIVE",
                "state": "EARLY_CANDIDATE",
            },
        )
    )
    assert signal is not None
    assert signal.action == "EARLY"
    assert signal.action_label == "РАННИЙ СИГНАЛ"
    assert signal.why[0].startswith("катализатор:")


def test_usable_but_not_ready_name_remains_watch():
    signal = _signal(
        _item(
            score=59.0,
            fundamental_score=61.0,
            technical_score=48.0,
            technical_state="нейтральный D1",
        )
    )
    assert signal is not None
    assert signal.action == "WATCH"


def test_ineligible_name_never_becomes_signal_even_with_high_score():
    assert _signal(_item(score=95.0, eligible=False)) is None


def test_descending_d1_cannot_be_accumulate():
    signal = _signal(
        _item(
            score=80.0,
            fundamental_score=85.0,
            technical_score=65.0,
            technical_state="нисходящий D1",
        )
    )
    assert signal is not None
    assert signal.action == "WATCH"
    assert any("нисходящий" in risk.lower() for risk in signal.risks)
