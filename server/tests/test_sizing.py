"""Риск и размер позиции §17, §17.1, §20.1 — здесь считаются деньги."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.risk.sizing import (
    InstrumentSpec,
    RiskLimits,
    RiskState,
    compute_budget,
    drawdown_multiplier,
    option_contracts,
    risk_per_unit,
    score_risk_percent,
    size_position,
)

LIM = RiskLimits()
# Фьючерс Si: шаг 1 рубль, стоимость шага 1 рубль.
SI = InstrumentSpec(tick_size=Decimal(1), tick_value=Decimal(1))
# Крипта: объём считается прямо из разницы цен, шаг 0.001.
BTC = InstrumentSpec(
    tick_size=Decimal("0.1"), tick_value=Decimal("0.1"),
    quantity_step=Decimal("0.001"), min_quantity=Decimal("0.001"), is_linear=True,
)


def state(**kw) -> RiskState:
    base = dict(risk_equity=Decimal(1_000_000))
    base.update(kw)
    return RiskState(**base)


# ─── Ступени риска и просадка §17 ─────────────────────────────────────────


def test_score_risk_has_two_steps_not_three():
    """Engine-ТЗ знает 0,5% и 0,75%. Ступени 1% из UX-ТЗ здесь нет."""
    assert score_risk_percent(Decimal(70), LIM)[0] == Decimal("0.005")
    assert score_risk_percent(Decimal(85), LIM)[0] == Decimal("0.0075")
    assert score_risk_percent(Decimal(99), LIM)[0] == Decimal("0.0075")


@pytest.mark.parametrize(
    ("drawdown", "expected"),
    [
        ("0.00", "1.00"), ("0.04", "1.00"),
        ("0.05", "0.75"), ("0.07", "0.75"),
        ("0.08", "0.50"), ("0.10", "0.50"),
        ("0.11", "0.00"), ("0.30", "0.00"),
    ],
)
def test_drawdown_ladder_matches_tz(drawdown, expected):
    assert drawdown_multiplier(Decimal(drawdown)) == Decimal(expected)


def test_deep_drawdown_is_a_halt_not_a_small_size():
    """Больше 10% — остановка. Не «очень маленький объём»."""
    budget = compute_budget(score=Decimal(90), state=state(current_drawdown=Decimal("0.15")))
    assert budget.halted and budget.amount == 0
    sizing = size_position(
        budget=budget, entry=Decimal(90000), stop=Decimal(89000), spec=SI
    )
    assert not sizing.tradable
    assert "остановлена" in sizing.reason


# ─── Бюджет — минимум из всех ограничений §20.1 ───────────────────────────


def test_budget_is_the_minimum_and_names_the_binding_limit():
    """Урезанный объём без названной причины выглядит как ошибка."""
    budget = compute_budget(
        score=Decimal(90),
        state=state(day_pnl_pct=Decimal("-0.013")),   # осталось 0,002 из 0,015
    )
    assert budget.binding == "daily"
    assert budget.percent == Decimal("0.002")
    assert "израсходовано" in next(
        line.detail for line in budget.lines if line.name == "daily"
    )


def test_cluster_limit_can_be_the_binding_one():
    budget = compute_budget(
        score=Decimal(90), state=state(cluster_risk_pct=Decimal("0.0095"))
    )
    assert budget.binding == "cluster"
    assert budget.percent == Decimal("0.0005")


def test_open_risk_limit_can_be_the_binding_one():
    budget = compute_budget(
        score=Decimal(90), state=state(open_risk_pct=Decimal("0.0195"))
    )
    assert budget.binding == "open"


def test_exhausted_limit_blocks_entry():
    budget = compute_budget(
        score=Decimal(90), state=state(day_pnl_pct=Decimal("-0.02"))
    )
    assert budget.blocked
    assert budget.percent == 0


def test_profit_does_not_expand_the_limit():
    """Прибыль не восстанавливает израсходованный лимит убытка."""
    with_profit = compute_budget(score=Decimal(70), state=state(day_pnl_pct=Decimal("0.05")))
    flat = compute_budget(score=Decimal(70), state=state())
    assert with_profit.percent == flat.percent


def test_strategy_multiplier_lowers_risk():
    """§12.4: разворот Вайкоффа рискует меньше — множитель 0,75."""
    normal = compute_budget(score=Decimal(90), state=state())
    wyckoff = compute_budget(
        score=Decimal(90), state=state(), strategy_multiplier=Decimal("0.75")
    )
    assert wyckoff.percent == normal.percent * Decimal("0.75")


def test_all_limits_are_reported_not_just_the_binding_one():
    budget = compute_budget(score=Decimal(80), state=state())
    assert {line.name for line in budget.lines} == {
        "score", "daily", "weekly", "monthly", "open", "cluster"
    }


# ─── Риск на единицу §17.1 ────────────────────────────────────────────────


def test_futures_risk_uses_tick_value_not_price_difference():
    """«Сколько рублей стоит пункт» у контракта своё."""
    spec = InstrumentSpec(tick_size=Decimal(1), tick_value=Decimal(10))
    assert risk_per_unit(entry=Decimal(90000), stop=Decimal(89900), spec=spec) == Decimal(1000)


def test_linear_instrument_uses_price_difference():
    assert risk_per_unit(entry=Decimal(60000), stop=Decimal(59000), spec=BTC) == Decimal(1000)


def test_costs_are_part_of_risk_per_unit():
    """Без комиссий расчётный убыток меньше фактического, и позиция крупнее."""
    bare = risk_per_unit(entry=Decimal(90000), stop=Decimal(89000), spec=SI)
    with_costs = risk_per_unit(
        entry=Decimal(90000), stop=Decimal(89000), spec=SI,
        slippage=Decimal(50), fees=Decimal(30),
    )
    assert with_costs == bare + Decimal(80)


def test_zero_stop_distance_is_an_error():
    with pytest.raises(ValueError, match="совпадает с входом"):
        risk_per_unit(entry=Decimal(100), stop=Decimal(100), spec=SI)


# ─── Объём §17.1, §20.1 ───────────────────────────────────────────────────


def test_quantity_is_floored_never_rounded_up():
    """Округление вверх превышает бюджет, только что посчитанный как минимум."""
    budget = compute_budget(score=Decimal(70), state=state())   # 0,5% от 1 млн = 5000
    sizing = size_position(
        budget=budget, entry=Decimal(90000), stop=Decimal(88800), spec=SI
    )
    # 5000 / 1200 = 4,166… → 4 контракта
    assert sizing.quantity == Decimal(4)
    assert sizing.risk_amount == Decimal(4800)
    assert sizing.risk_amount <= budget.amount


def test_crypto_quantity_respects_step():
    budget = compute_budget(score=Decimal(70), state=state())
    sizing = size_position(
        budget=budget, entry=Decimal(60000), stop=Decimal(57000), spec=BTC
    )
    # 5000 / 3000 = 1,666… → шаг 0.001 → 1.666
    assert sizing.quantity == Decimal("1.666")
    assert sizing.tradable


def test_too_small_size_makes_idea_informational():
    """§20.1 дословно: объём меньше лота — идея не подтверждается.

    Не «округляем до одного контракта»: один контракт может стоить втрое
    дороже допустимого риска.
    """
    budget = compute_budget(
        score=Decimal(70), state=state(risk_equity=Decimal(10_000))
    )   # бюджет 50 рублей
    sizing = size_position(
        budget=budget, entry=Decimal(90000), stop=Decimal(88000), spec=SI
    )
    assert not sizing.tradable
    assert sizing.quantity == 0
    assert "информационная" in sizing.reason


def test_leverage_above_limit_is_refused():
    budget = compute_budget(score=Decimal(70), state=state())
    sizing = size_position(
        budget=budget, entry=Decimal(60000), stop=Decimal(59000), spec=BTC,
        leverage=Decimal(10),
    )
    assert not sizing.tradable and "плечо" in sizing.reason


def test_liquidation_must_be_further_than_stop():
    """§13: иначе биржа закроет позицию раньше стопа, и стоп бесполезен."""
    budget = compute_budget(score=Decimal(70), state=state())
    sizing = size_position(
        budget=budget, entry=Decimal(60000), stop=Decimal(59000), spec=BTC,
        liquidation_price=Decimal(58000),   # 2000 против 1000 — нужно 2500
    )
    assert not sizing.tradable
    assert "ликвидация" in sizing.reason


def test_sufficient_liquidation_distance_passes():
    budget = compute_budget(score=Decimal(70), state=state())
    sizing = size_position(
        budget=budget, entry=Decimal(60000), stop=Decimal(59000), spec=BTC,
        liquidation_price=Decimal(56000),
    )
    assert sizing.tradable


def test_min_notional_is_respected():
    spec = InstrumentSpec(
        tick_size=Decimal("0.1"), tick_value=Decimal("0.1"),
        quantity_step=Decimal("0.001"), min_quantity=Decimal("0.001"),
        min_notional=Decimal(10**9), is_linear=True,
    )
    budget = compute_budget(score=Decimal(70), state=state())
    sizing = size_position(
        budget=budget, entry=Decimal(60000), stop=Decimal(57000), spec=spec
    )
    assert not sizing.tradable and "номинал" in sizing.reason


def test_everything_is_decimal():
    budget = compute_budget(score=Decimal(70), state=state())
    sizing = size_position(
        budget=budget, entry=Decimal(90000), stop=Decimal(88800), spec=SI
    )
    for value in (sizing.quantity, sizing.risk_amount, sizing.risk_per_unit, sizing.notional):
        assert isinstance(value, Decimal)


# ─── Опционы §17.1, §14 ───────────────────────────────────────────────────


def test_option_without_known_max_loss_is_refused():
    """§1.2 и §14: конструкции с неограниченным риском запрещены."""
    budget = compute_budget(score=Decimal(70), state=state())
    with pytest.raises(ValueError, match="max loss"):
        option_contracts(budget=budget, max_loss_per_contract=Decimal(0))


def test_option_contracts_are_floored():
    budget = compute_budget(score=Decimal(70), state=state())   # 5000
    sizing = option_contracts(budget=budget, max_loss_per_contract=Decimal(1200))
    assert sizing.quantity == Decimal(4)
    assert sizing.risk_amount == Decimal(4800)


def test_option_below_one_contract_is_informational():
    budget = compute_budget(score=Decimal(70), state=state(risk_equity=Decimal(10_000)))
    sizing = option_contracts(budget=budget, max_loss_per_contract=Decimal(1200))
    assert not sizing.tradable and "информационная" in sizing.reason
