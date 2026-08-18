from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtest.costs import CostModel
from app.backtest.robustness import PathObservation, compute_robustness_report
from app.backtest.stress import (
    StressScenario,
    default_stress_scenarios,
    run_stress_matrix,
)


BASE = datetime(2026, 8, 18, tzinfo=UTC)


def cost_model() -> CostModel:
    return CostModel(
        maker_fee_bps=Decimal("1"),
        taker_fee_bps=Decimal("2"),
        entry_slippage_bps=Decimal("3"),
        exit_slippage_bps=Decimal("4"),
        funding_bps_per_interval=Decimal("0.5"),
        spread_bps=Decimal("2"),
    )


def report(net_shift: Decimal = Decimal("0")):
    rows = (
        PathObservation(
            at=BASE,
            gross_return=Decimal("0.030"),
            net_return=Decimal("0.025") - net_shift,
            turnover=Decimal("0.4"),
            mae=Decimal("-0.01"),
            mfe=Decimal("0.04"),
            regime="TREND",
        ),
        PathObservation(
            at=BASE + timedelta(days=1),
            gross_return=Decimal("-0.005"),
            net_return=Decimal("-0.010") - net_shift,
            turnover=Decimal("0.2"),
            mae=Decimal("-0.02"),
            mfe=Decimal("0.01"),
            regime="RANGE",
        ),
        PathObservation(
            at=BASE + timedelta(days=2),
            gross_return=Decimal("0.020"),
            net_return=Decimal("0.015") - net_shift,
            turnover=Decimal("0.3"),
            mae=Decimal("-0.01"),
            mfe=Decimal("0.03"),
            regime="HIGH_VOLATILITY",
        ),
    )
    return compute_robustness_report(rows, periods_per_year=Decimal("252"))


def test_default_matrix_contains_every_required_stress_once_in_stable_order():
    scenarios = default_stress_scenarios()

    assert tuple(item.name for item in scenarios) == (
        "base",
        "costs_x1_5",
        "costs_x2",
        "latency_slippage",
        "liquidity_haircut",
        "missing_data",
        "high_volatility",
    )
    assert len({item.name for item in scenarios}) == len(scenarios)


def test_cost_stress_is_explicit_composable_and_does_not_mutate_base_model():
    base = cost_model()
    scenarios = {item.name: item for item in default_stress_scenarios()}

    x15 = scenarios["costs_x1_5"].apply_cost_model(base)
    x2 = scenarios["costs_x2"].apply_cost_model(base)
    latency = scenarios["latency_slippage"].apply_cost_model(base)

    assert x15.taker_fee_bps == Decimal("3.0")
    assert x15.entry_slippage_bps == Decimal("4.5")
    assert x2.taker_fee_bps == Decimal("4")
    assert x2.funding_bps_per_interval == Decimal("1.0")
    assert latency.taker_fee_bps == base.taker_fee_bps
    assert latency.entry_slippage_bps == Decimal("6")
    assert latency.exit_slippage_bps == Decimal("8")
    assert scenarios["latency_slippage"].latency_bars == 1
    assert base.taker_fee_bps == Decimal("2")
    assert base.entry_slippage_bps == Decimal("3")


def test_non_cost_stresses_are_machine_readable_and_bounded():
    scenarios = {item.name: item for item in default_stress_scenarios()}

    assert scenarios["liquidity_haircut"].liquidity_haircut == Decimal("0.5")
    assert scenarios["missing_data"].missing_data_fraction == Decimal("0.10")
    assert scenarios["high_volatility"].regime_filter == "HIGH_VOLATILITY"


def test_runner_returns_comparable_deltas_to_the_single_base_scenario():
    base_model = cost_model()
    seen: list[tuple[str, CostModel]] = []

    def evaluator(scenario: StressScenario, stressed_costs: CostModel):
        seen.append((scenario.name, stressed_costs))
        penalty = {
            "base": Decimal("0"),
            "costs_x1_5": Decimal("0.001"),
            "costs_x2": Decimal("0.002"),
            "latency_slippage": Decimal("0.003"),
            "liquidity_haircut": Decimal("0.004"),
            "missing_data": Decimal("0.005"),
            "high_volatility": Decimal("0.006"),
        }[scenario.name]
        return report(penalty)

    result = run_stress_matrix(
        default_stress_scenarios(),
        base_cost_model=base_model,
        evaluator=evaluator,
    )

    assert tuple(item.scenario.name for item in result.results) == tuple(
        item.name for item in default_stress_scenarios()
    )
    assert len(seen) == 7
    assert result.base.scenario.name == "base"
    assert result.by_name("costs_x2").expectancy_delta < 0
    assert result.by_name("high_volatility").net_total_return_delta < 0
    assert result.by_name("base").expectancy_delta == pytest.approx(0.0)
    assert result.by_name("base").max_drawdown_delta == pytest.approx(0.0)


def test_runner_rejects_missing_or_duplicate_base_and_duplicate_names():
    base = StressScenario(name="base")
    duplicate = StressScenario(name="base", cost_multiplier=Decimal("1.5"))

    with pytest.raises(ValueError, match="exactly one base"):
        run_stress_matrix(
            (StressScenario(name="costs"),),
            base_cost_model=cost_model(),
            evaluator=lambda scenario, costs: report(),
        )
    with pytest.raises(ValueError, match="scenario names"):
        run_stress_matrix(
            (base, duplicate),
            base_cost_model=cost_model(),
            evaluator=lambda scenario, costs: report(),
        )


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"cost_multiplier": Decimal("0")}, "cost_multiplier"),
        ({"slippage_multiplier": Decimal("0")}, "slippage_multiplier"),
        ({"latency_bars": -1}, "latency_bars"),
        ({"liquidity_haircut": Decimal("0")}, "liquidity_haircut"),
        ({"liquidity_haircut": Decimal("1.1")}, "liquidity_haircut"),
        ({"missing_data_fraction": Decimal("1")}, "missing_data_fraction"),
        ({"missing_data_fraction": Decimal("-0.1")}, "missing_data_fraction"),
    ],
)
def test_scenario_parameters_fail_closed_outside_safe_ranges(kwargs, message):
    with pytest.raises(ValueError, match=message):
        StressScenario(name="invalid", **kwargs)
