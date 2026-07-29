"""Оценка §15.1, вероятность §15.2–15.6: числа и то, что они означают."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.market.candles import Candle
from app.models.enums import BarrierOutcome, Direction, LiquidityRegime, QualityStatus
from app.probability.barrier import (
    ConfidenceInput,
    confidence,
    empirical,
    expected_value,
    label_triple_barrier,
    rule_prior,
)
from app.scoring.admission import AdmissionThresholds, admit
from app.scoring.score import WEIGHTS, Component, compute

START = datetime(2026, 7, 1, tzinfo=UTC)


def full(value: str = "80", **overrides) -> list[Component]:
    """Полный набор из одиннадцати компонентов."""
    return [
        Component(name, Decimal(overrides.get(name, value)))
        if name not in overrides or isinstance(overrides[name], str)
        else overrides[name]
        for name in WEIGHTS
    ]


# ─── Оценка §15.1 ─────────────────────────────────────────────────────────


def test_weights_match_tz_and_sum_to_one():
    assert sum(WEIGHTS.values()) == Decimal("1")
    assert WEIGHTS["regime_fit"] == Decimal("0.16")
    assert WEIGHTS["execution_quality"] == Decimal("0.03")
    assert "data_quality" not in WEIGHTS


def test_perfect_components_give_hundred():
    score = compute(full("100"))
    assert score.raw == Decimal("100.00")
    assert score.data_quality == Decimal("1.000")
    assert score.total == Decimal("100.00")


def test_missing_component_is_an_error_not_a_zero():
    """Молчаливый пропуск читался бы как «измерено и плохо»."""
    partial = [c for c in full() if c.name != "liquidity_quality"]
    with pytest.raises(ValueError, match="не переданы компоненты"):
        compute(partial)


def test_unknown_component_is_rejected():
    with pytest.raises(ValueError, match="неизвестные компоненты"):
        compute([*full(), Component("rsi", Decimal(50))])


def test_value_out_of_range_is_rejected():
    with pytest.raises(ValueError, match="вне 0..100"):
        compute(full(liquidity_quality=Component("liquidity_quality", Decimal(140))))


def test_not_applicable_redistributes_weight():
    """Открытого интереса у акции не бывает — оценка не должна за это падать."""
    baseline = compute(full("80"))
    without_oi = compute(
        full(
            "80",
            derivatives_confirmation=Component(
                "derivatives_confirmation", Decimal(0),
                status="not_applicable", note="у акции нет открытого интереса",
            ),
        )
    )
    assert without_oi.raw == baseline.raw          # вес перераспределён
    assert without_oi.data_quality == Decimal("1.000")  # доверие не пострадало
    assert "derivatives_confirmation" in without_oi.not_applicable


def test_missing_lowers_both_score_and_data_quality():
    """Данные бывают, но не получены — это ноль и просадка доверия."""
    baseline = compute(full("80"))
    broken = compute(
        full(
            "80",
            derivatives_confirmation=Component(
                "derivatives_confirmation", Decimal(0),
                status="missing", note="запрос открытого интереса не прошёл",
            ),
        )
    )
    assert broken.raw < baseline.raw
    assert broken.data_quality < Decimal("1.000")
    assert "derivatives_confirmation" in broken.missing
    assert any("не измерено" in n for n in broken.notes)


def test_not_applicable_and_missing_are_not_the_same():
    """Смешение этих состояний превращает отказ источника в подтверждение."""
    na = compute(
        full("80", derivatives_confirmation=Component(
            "derivatives_confirmation", Decimal(0), status="not_applicable"))
    )
    missing = compute(
        full("80", derivatives_confirmation=Component(
            "derivatives_confirmation", Decimal(0), status="missing"))
    )
    assert na.total > missing.total


def test_data_quality_is_a_multiplier_not_a_component():
    """Плохие данные портят всю оценку, а не отнимают у неё несколько баллов."""
    score = compute(
        full("100", regime_fit=Component("regime_fit", Decimal(0), status="missing"))
    )
    assert score.total < score.raw
    assert score.total == (score.raw * score.data_quality).quantize(Decimal("0.01"))


def test_data_quality_never_below_floor():
    every_missing = [
        Component(name, Decimal(0), status="missing", note="нет данных")
        for name in WEIGHTS
    ]
    score = compute(every_missing)
    assert score.data_quality == Decimal("0.500")


def test_heavier_component_hurts_trust_more():
    """Вес 0,16 портит доверие сильнее, чем вес 0,03."""
    heavy = compute(full("80", regime_fit=Component("regime_fit", Decimal(0), status="missing")))
    light = compute(
        full("80", execution_quality=Component(
            "execution_quality", Decimal(0), status="missing"))
    )
    assert heavy.data_quality < light.data_quality


def test_components_carry_evidence_reference():
    """§9.1: у вклада должна быть ссылка на доказательство."""
    score = compute(
        full("80", regime_fit=Component("regime_fit", Decimal(90), evidence_id="ev-1"))
    )
    assert score.component("regime_fit").evidence_id == "ev-1"


# ─── Тройной барьер §15.2 ─────────────────────────────────────────────────


def c(i: int, h: str, low: str, cl: str = "100") -> Candle:
    return Candle(
        START + timedelta(hours=i), Decimal(cl), Decimal(h), Decimal(low),
        Decimal(cl), source="t",
    )


def test_win_when_tp_comes_first():
    bars = [c(0, "101", "99"), c(1, "104", "99"), c(2, "105", "95")]
    result = label_triple_barrier(
        bars, direction=Direction.LONG, entry_index=0,
        take_profit=Decimal(103), stop_loss=Decimal(97), horizon_bars=5,
    )
    assert result.outcome is BarrierOutcome.WIN
    assert result.bars_to_resolution == 1


def test_loss_when_stop_comes_first():
    bars = [c(0, "101", "99"), c(1, "101", "96"), c(2, "105", "95")]
    result = label_triple_barrier(
        bars, direction=Direction.LONG, entry_index=0,
        take_profit=Decimal(103), stop_loss=Decimal(97), horizon_bars=5,
    )
    assert result.outcome is BarrierOutcome.LOSS


def test_timeout_when_neither_barrier_hit():
    bars = [c(i, "101", "99") for i in range(6)]
    result = label_triple_barrier(
        bars, direction=Direction.LONG, entry_index=0,
        take_profit=Decimal(103), stop_loss=Decimal(97), horizon_bars=3,
    )
    assert result.outcome is BarrierOutcome.TIMEOUT
    assert result.bars_to_resolution is None


def test_ambiguous_bar_is_not_a_usable_label():
    """§15.2: оба барьера в одной свече — метку для обучения брать нельзя.

    Подставить «наверное, сначала TP» значило бы завышать вероятность ровно
    на самых волатильных барах, где ошибка дороже всего.
    """
    bars = [c(0, "101", "99"), c(1, "105", "95")]
    result = label_triple_barrier(
        bars, direction=Direction.LONG, entry_index=0,
        take_profit=Decimal(103), stop_loss=Decimal(97), horizon_bars=5,
    )
    assert result.outcome is BarrierOutcome.AMBIGUOUS
    assert result.label_usable is False
    assert "младшего таймфрейма нет" in result.detail


def test_short_direction_is_mirrored():
    bars = [c(0, "101", "99"), c(1, "101", "96")]
    result = label_triple_barrier(
        bars, direction=Direction.SHORT, entry_index=0,
        take_profit=Decimal(97), stop_loss=Decimal(103), horizon_bars=5,
    )
    assert result.outcome is BarrierOutcome.WIN


# ─── Вероятность §15.3 ────────────────────────────────────────────────────


def test_rule_prior_is_capped():
    """Пока нет ста OOS-сделок, 0,68 и 0,80 различить не на чем."""
    top = rule_prior(Decimal(100))
    assert top.p_tp1_before_sl == Decimal("0.6800")
    assert top.capped and "100 релевантных OOS" in top.cap_reason
    assert top.source == "rule_prior"


def test_rule_prior_grows_with_score_but_is_not_the_score():
    low = rule_prior(Decimal(60)).p_tp1_before_sl
    high = rule_prior(Decimal(90)).p_tp1_before_sl
    assert low < high
    # Балл 90 не означает вероятность 0,90.
    assert high < Decimal("0.70")


def test_empirical_shrinks_small_samples():
    """Три победы из четырёх — это не 75%, это «мы почти ничего не знаем»."""
    estimate = empirical(3, 4, prior=Decimal("0.5"))
    assert Decimal("0.50") < estimate.p_tp1_before_sl < Decimal("0.62")
    assert estimate.capped


def test_empirical_approaches_frequency_on_large_sample():
    estimate = empirical(600, 1000, prior=Decimal("0.5"), min_sample=100)
    assert Decimal("0.59") < estimate.p_tp1_before_sl < Decimal("0.60")
    assert not estimate.capped


def test_empirical_rejects_impossible_sample():
    with pytest.raises(ValueError):
        empirical(5, 3, prior=Decimal("0.5"))


def test_probability_definition_travels_with_the_number():
    """§32: без определения 0,61 читается как «шанс заработать»."""
    estimate = rule_prior(Decimal(80))
    assert "TP1" in estimate.definition and "SL" in estimate.definition


# ─── Уверенность §15.4 ────────────────────────────────────────────────────


def test_confidence_weights_match_tz():
    value, band, terms = confidence(
        ConfidenceInput(Decimal(1), Decimal(1), Decimal(1), Decimal(1), Decimal(1))
    )
    assert value == Decimal("1.0000")
    assert band == "HIGH"
    assert [w for _, w, _ in terms] == [
        Decimal("0.30"), Decimal("0.25"), Decimal("0.20"),
        Decimal("0.15"), Decimal("0.10"),
    ]


def test_confidence_is_low_without_sample():
    value, band, _ = confidence(
        ConfidenceInput(Decimal(0), Decimal(0), Decimal(1), Decimal("0.5"), Decimal(0))
    )
    assert band == "LOW"
    assert value < Decimal("0.45")


def test_confidence_rejects_values_out_of_range():
    with pytest.raises(ValueError):
        confidence(ConfidenceInput(Decimal(2), Decimal(0), Decimal(0), Decimal(0), Decimal(0)))


# ─── Ожидание §15.5 ───────────────────────────────────────────────────────


def test_expected_value_subtracts_costs():
    """Ожидание «до комиссий» — приглашение торговать в минус."""
    gross = expected_value(
        p_win=Decimal("0.6"), avg_win_r=Decimal(2), avg_loss_r=Decimal(1)
    )
    net = expected_value(
        p_win=Decimal("0.6"), avg_win_r=Decimal(2), avg_loss_r=Decimal(1),
        costs_r=Decimal("0.1"),
    )
    assert gross == Decimal("0.8000")
    assert net == Decimal("0.7000")


def test_expected_value_accounts_for_timeout():
    value = expected_value(
        p_win=Decimal("0.5"), avg_win_r=Decimal(2), avg_loss_r=Decimal(1),
        p_timeout=Decimal("0.2"), timeout_cost_r=Decimal("0.1"),
    )
    assert value == Decimal("0.6800")


def test_expected_value_rejects_impossible_probabilities():
    with pytest.raises(ValueError):
        expected_value(
            p_win=Decimal("0.9"), avg_win_r=Decimal(2), avg_loss_r=Decimal(1),
            p_timeout=Decimal("0.3"),
        )


# ─── Допуск §15.6 ─────────────────────────────────────────────────────────


GOOD = dict(
    probability=Decimal("0.60"), expected_r=Decimal("0.30"), rr_tp2=Decimal("2.4"),
    confidence=Decimal("0.55"), liquidity=LiquidityRegime.GOOD,
    has_trigger=True, risk_blocked=False,
)


def test_active_when_all_conditions_hold():
    result = admit(**GOOD)
    assert result.status is QualityStatus.ACTIVE
    assert result.failed == ()


def test_watch_when_trigger_is_missing():
    result = admit(**{**GOOD, "has_trigger": False})
    assert result.status is QualityStatus.WATCH
    assert "триггер" in result.reason.lower()


def test_rejected_on_negative_expectancy():
    """Отрицательное ожидание — отказ, а не наблюдение: ждать нечего."""
    result = admit(**{**GOOD, "expected_r": Decimal("-0.1")})
    assert result.status is QualityStatus.REJECTED


def test_risk_block_wins_over_everything():
    """Отклонение по риску не должно теряться среди рассуждений о вероятности."""
    result = admit(
        **{**GOOD, "risk_blocked": True, "risk_block_reason": "дневной лимит выбран"}
    )
    assert result.status is QualityStatus.REJECTED
    assert "дневной лимит" in result.reason


def test_untradeable_liquidity_rejects():
    result = admit(**{**GOOD, "liquidity": LiquidityRegime.UNTRADEABLE})
    assert result.status is QualityStatus.REJECTED
    assert "UNTRADEABLE" in result.reason


def test_every_gate_is_reported_with_its_number():
    """«Отклонено» без объяснения не позволяет ни поспорить, ни починить."""
    result = admit(**{**GOOD, "rr_tp2": Decimal("1.5")})
    rr = next(g for g in result.gates if g.name == "rr")
    assert not rr.passed
    assert "1.5" in rr.detail and "2.0" in rr.detail


def test_thresholds_come_from_config_not_code():
    strict = AdmissionThresholds(active_probability_min=Decimal("0.90"))
    assert admit(**GOOD, thresholds=strict).status is not QualityStatus.ACTIVE
