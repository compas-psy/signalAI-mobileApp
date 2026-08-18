"""Deterministic stress-matrix orchestration for offline strategy research.

Scenarios are immutable, machine-readable perturbation requests.  This module
owns neither market simulation nor strategy execution: a caller supplies the
actual evaluator, which receives the scenario plus a derived ``CostModel``.
That separation prevents the robustness layer from inventing a second set of
execution semantics while still making every required stress explicit and
comparable with one neutral base run.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Callable, Sequence

from .costs import CostModel
from .robustness import RobustnessReport


_ONE = Decimal("1")
_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class StressScenario:
    name: str
    cost_multiplier: Decimal = _ONE
    slippage_multiplier: Decimal = _ONE
    latency_bars: int = 0
    liquidity_haircut: Decimal = _ONE
    missing_data_fraction: Decimal = _ZERO
    regime_filter: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("stress scenario name is required")
        if self.cost_multiplier <= 0:
            raise ValueError("cost_multiplier must be positive")
        if self.slippage_multiplier <= 0:
            raise ValueError("slippage_multiplier must be positive")
        if self.latency_bars < 0:
            raise ValueError("latency_bars must be non-negative")
        if not (_ZERO < self.liquidity_haircut <= _ONE):
            raise ValueError("liquidity_haircut must be in (0, 1]")
        if not (_ZERO <= self.missing_data_fraction < _ONE):
            raise ValueError("missing_data_fraction must be in [0, 1)")
        if self.regime_filter is not None and not self.regime_filter.strip():
            raise ValueError("regime_filter must be non-empty when supplied")

    @property
    def is_neutral(self) -> bool:
        return (
            self.cost_multiplier == _ONE
            and self.slippage_multiplier == _ONE
            and self.latency_bars == 0
            and self.liquidity_haircut == _ONE
            and self.missing_data_fraction == _ZERO
            and self.regime_filter is None
        )

    def apply_cost_model(self, base: CostModel) -> CostModel:
        """Derive scenario costs without mutating the caller's base model."""

        stressed = base.stressed(self.cost_multiplier)
        if self.slippage_multiplier == _ONE:
            return stressed
        return replace(
            stressed,
            entry_slippage_bps=(
                stressed.entry_slippage_bps * self.slippage_multiplier
            ),
            exit_slippage_bps=(
                stressed.exit_slippage_bps * self.slippage_multiplier
            ),
        )


@dataclass(frozen=True, slots=True)
class StressScenarioResult:
    scenario: StressScenario
    report: RobustnessReport
    expectancy_delta: float
    net_total_return_delta: float
    max_drawdown_delta: float


@dataclass(frozen=True, slots=True)
class StressMatrixResult:
    results: tuple[StressScenarioResult, ...]

    @property
    def base(self) -> StressScenarioResult:
        return self.by_name("base")

    def by_name(self, name: str) -> StressScenarioResult:
        for item in self.results:
            if item.scenario.name == name:
                return item
        raise KeyError(f"stress scenario {name!r} not found")


def default_stress_scenarios() -> tuple[StressScenario, ...]:
    """Return the stable B2.4 stress matrix in review/reporting order."""

    return (
        StressScenario(name="base"),
        StressScenario(name="costs_x1_5", cost_multiplier=Decimal("1.5")),
        StressScenario(name="costs_x2", cost_multiplier=Decimal("2")),
        StressScenario(
            name="latency_slippage",
            latency_bars=1,
            slippage_multiplier=Decimal("2"),
        ),
        StressScenario(
            name="liquidity_haircut",
            liquidity_haircut=Decimal("0.5"),
        ),
        StressScenario(
            name="missing_data",
            missing_data_fraction=Decimal("0.10"),
        ),
        StressScenario(name="high_volatility", regime_filter="HIGH_VOLATILITY"),
    )


def run_stress_matrix(
    scenarios: Sequence[StressScenario],
    *,
    base_cost_model: CostModel,
    evaluator: Callable[[StressScenario, CostModel], RobustnessReport],
) -> StressMatrixResult:
    """Evaluate each scenario exactly once and report deltas to neutral base."""

    ordered = tuple(scenarios)
    if not ordered:
        raise ValueError("at least one stress scenario is required")
    names = [scenario.name for scenario in ordered]
    if len(names) != len(set(names)):
        raise ValueError("stress scenario names must be unique")
    bases = [scenario for scenario in ordered if scenario.name == "base"]
    if len(bases) != 1:
        raise ValueError("stress matrix requires exactly one base scenario")
    if not bases[0].is_neutral:
        raise ValueError("base scenario must be neutral")

    evaluated: list[tuple[StressScenario, RobustnessReport]] = []
    for scenario in ordered:
        scenario_costs = scenario.apply_cost_model(base_cost_model)
        report = evaluator(scenario, scenario_costs)
        if not isinstance(report, RobustnessReport):
            raise TypeError("stress evaluator must return RobustnessReport")
        evaluated.append((scenario, report))

    base_report = next(
        report for scenario, report in evaluated if scenario.name == "base"
    )
    return StressMatrixResult(
        results=tuple(
            StressScenarioResult(
                scenario=scenario,
                report=report,
                expectancy_delta=report.expectancy - base_report.expectancy,
                net_total_return_delta=(
                    report.net_total_return - base_report.net_total_return
                ),
                max_drawdown_delta=report.max_drawdown - base_report.max_drawdown,
            )
            for scenario, report in evaluated
        )
    )


__all__ = [
    "StressMatrixResult",
    "StressScenario",
    "StressScenarioResult",
    "default_stress_scenarios",
    "run_stress_matrix",
]
