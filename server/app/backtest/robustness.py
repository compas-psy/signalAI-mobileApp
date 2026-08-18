"""Offline robustness metrics for research and promotion evidence.

The module deliberately consumes realised path observations and synchronous
strategy-return matrices.  It is measurement-only: scanner eligibility,
strategy math, risk, paper lifecycle, notifications and execution do not import
or depend on it.

Deflated Sharpe follows Bailey & Lopez de Prado (2014): the observed Sharpe is
compared with the expected maximum Sharpe across multiple trials, then Eq. 2
maps the deflated statistic through the standard Normal CDF while accounting
for sample length, skewness and non-excess kurtosis.

Probability of Backtest Overfitting follows Bailey, Borwein, Lopez de Prado &
Zhu's CSCV procedure: contiguous observations are split into an even number of
equal partitions; every half-partition IS combination is paired with its OOS
complement; the IS winner's relative OOS rank is transformed into a logit; PBO
is the fraction of those logits below zero.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import NormalDist
from typing import Iterable, Sequence


_EULER_MASCHERONI = 0.5772156649
_NORMAL = NormalDist()


@dataclass(frozen=True, slots=True)
class PathObservation:
    at: datetime
    gross_return: Decimal
    net_return: Decimal
    turnover: Decimal
    mae: Decimal
    mfe: Decimal
    regime: str
    predicted_positive_probability: Decimal | None = None
    positive_outcome: bool | None = None

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise ValueError("path observation time must be timezone-aware")
        if self.gross_return <= Decimal("-1") or self.net_return <= Decimal("-1"):
            raise ValueError("returns must stay above -100%")
        if self.net_return > self.gross_return:
            raise ValueError("net_return cannot exceed gross_return with non-negative costs")
        if self.turnover < 0:
            raise ValueError("turnover must be non-negative")
        if self.mae > 0:
            raise ValueError("mae must be non-positive")
        if self.mfe < 0:
            raise ValueError("mfe must be non-negative")
        if not self.regime.strip():
            raise ValueError("regime is required")
        paired = (
            self.predicted_positive_probability is not None,
            self.positive_outcome is not None,
        )
        if paired[0] != paired[1]:
            raise ValueError("calibration probability and outcome must be supplied together")
        if self.predicted_positive_probability is not None and not (
            Decimal(0) <= self.predicted_positive_probability <= Decimal(1)
        ):
            raise ValueError("predicted probability must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class RobustnessReport:
    observations: int
    gross_total_return: float
    net_total_return: float
    annualized_return: float | None
    sharpe: float | None
    sortino: float | None
    max_drawdown: float
    calmar: float | None
    expectancy: float
    profit_factor: float | None
    win_rate: float
    payoff_ratio: float | None
    turnover: float
    cost_ratio: float | None
    average_mae: float
    average_mfe: float
    tail_loss_5pct: float
    longest_loss_streak: int
    pnl_concentration_hhi: float
    brier_score: float | None
    regime_expectancy: dict[str, float]
    worst_regime_expectancy: float


@dataclass(frozen=True, slots=True)
class DeflatedSharpeResult:
    expected_max_sharpe: float
    z_score: float
    probability: float


@dataclass(frozen=True, slots=True)
class PBOResult:
    combinations: int
    negative_logits: int
    probability: float
    logits: tuple[float, ...]


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values)


def _sample_std(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    value = statistics.stdev(values)
    return value if value > 0 else None


def _compound(returns: Sequence[float]) -> float:
    equity = 1.0
    for item in returns:
        equity *= 1.0 + item
    return equity - 1.0


def _max_drawdown(returns: Sequence[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for item in returns:
        equity *= 1.0 + item
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, 1.0 - equity / peak)
    return worst


def _longest_loss_streak(returns: Sequence[float]) -> int:
    longest = 0
    current = 0
    for item in returns:
        if item < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def compute_robustness_report(
    observations: Iterable[PathObservation],
    *,
    periods_per_year: Decimal,
) -> RobustnessReport:
    """Compute one auditable report from chronological realised observations."""

    rows = tuple(observations)
    if not rows:
        raise ValueError("at least one path observation is required")
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    times = [row.at for row in rows]
    if times != sorted(times):
        raise ValueError("path observations must be chronological")
    if len(times) != len(set(times)):
        raise ValueError("path observations must not duplicate timestamps")

    gross = [float(row.gross_return) for row in rows]
    net = [float(row.net_return) for row in rows]
    ppy = float(periods_per_year)
    n = len(net)

    gross_total = _compound(gross)
    net_total = _compound(net)
    expectancy = _mean(net)

    net_std = _sample_std(net)
    sharpe = (
        expectancy / net_std * math.sqrt(ppy)
        if net_std is not None
        else None
    )

    downside = [min(item, 0.0) for item in net]
    downside_deviation = math.sqrt(sum(item * item for item in downside) / n)
    sortino = (
        expectancy / downside_deviation * math.sqrt(ppy)
        if downside_deviation > 0
        else None
    )

    max_drawdown = _max_drawdown(net)
    ending_equity = 1.0 + net_total
    annualized_return = (
        ending_equity ** (ppy / n) - 1.0 if ending_equity > 0 else None
    )
    calmar = (
        annualized_return / max_drawdown
        if annualized_return is not None and max_drawdown > 0
        else None
    )

    wins = [item for item in net if item > 0]
    losses = [item for item in net if item < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    win_rate = len(wins) / n
    payoff_ratio = (
        _mean(wins) / abs(_mean(losses)) if wins and losses else None
    )

    turnover = sum(float(row.turnover) for row in rows)
    total_cost = sum(float(row.gross_return - row.net_return) for row in rows)
    gross_abs = sum(abs(item) for item in gross)
    cost_ratio = total_cost / gross_abs if gross_abs > 0 else None

    average_mae = _mean([float(row.mae) for row in rows])
    average_mfe = _mean([float(row.mfe) for row in rows])
    tail_count = max(1, math.ceil(0.05 * n))
    tail_loss = _mean(sorted(net)[:tail_count])

    absolute_net = [abs(item) for item in net]
    absolute_total = sum(absolute_net)
    concentration = (
        sum((item / absolute_total) ** 2 for item in absolute_net)
        if absolute_total > 0
        else 0.0
    )

    calibration_rows = [
        row
        for row in rows
        if row.predicted_positive_probability is not None
        and row.positive_outcome is not None
    ]
    brier = None
    if calibration_rows:
        if len(calibration_rows) != n:
            raise ValueError("calibration data must be complete for the whole report")
        brier = _mean(
            [
                (
                    float(row.predicted_positive_probability)
                    - (1.0 if row.positive_outcome else 0.0)
                )
                ** 2
                for row in calibration_rows
            ]
        )

    by_regime: dict[str, list[float]] = defaultdict(list)
    for row, value in zip(rows, net):
        by_regime[row.regime].append(value)
    regime_expectancy = {
        regime: _mean(values)
        for regime, values in sorted(by_regime.items())
    }
    worst_regime = min(regime_expectancy.values())

    return RobustnessReport(
        observations=n,
        gross_total_return=gross_total,
        net_total_return=net_total,
        annualized_return=annualized_return,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
        calmar=calmar,
        expectancy=expectancy,
        profit_factor=profit_factor,
        win_rate=win_rate,
        payoff_ratio=payoff_ratio,
        turnover=turnover,
        cost_ratio=cost_ratio,
        average_mae=average_mae,
        average_mfe=average_mfe,
        tail_loss_5pct=tail_loss,
        longest_loss_streak=_longest_loss_streak(net),
        pnl_concentration_hhi=concentration,
        brier_score=brier,
        regime_expectancy=regime_expectancy,
        worst_regime_expectancy=worst_regime,
    )


def deflated_sharpe_probability(
    *,
    observed_sharpe: Decimal,
    sample_length: int,
    skewness: Decimal,
    kurtosis: Decimal,
    independent_trials: int,
    trial_sharpe_std: Decimal,
) -> DeflatedSharpeResult:
    """Return Bailey/López de Prado Deflated Sharpe probability.

    ``kurtosis`` is the ordinary fourth standardized moment (Normal == 3), not
    excess kurtosis.  ``independent_trials`` is the effective number of trials;
    a caller that explored correlated variants should supply the estimated
    independent-trial count rather than raw variant count.
    """

    if sample_length <= 1:
        raise ValueError("sample_length must exceed one")
    if independent_trials <= 0:
        raise ValueError("independent_trials must be positive")
    sigma = float(trial_sharpe_std)
    if sigma < 0:
        raise ValueError("trial_sharpe_std must be non-negative")

    if independent_trials == 1 or sigma == 0:
        expected_max = 0.0
    else:
        n = float(independent_trials)
        max_z = (
            (1.0 - _EULER_MASCHERONI) * _NORMAL.inv_cdf(1.0 - 1.0 / n)
            + _EULER_MASCHERONI
            * _NORMAL.inv_cdf(1.0 - 1.0 / (n * math.e))
        )
        expected_max = sigma * max_z

    sr = float(observed_sharpe)
    skew = float(skewness)
    kurt = float(kurtosis)
    variance_term = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if variance_term <= 0 or not math.isfinite(variance_term):
        raise ValueError("Deflated Sharpe variance term must be positive and finite")

    z_score = (
        (sr - expected_max)
        * math.sqrt(sample_length - 1)
        / math.sqrt(variance_term)
    )
    probability = _NORMAL.cdf(z_score)
    return DeflatedSharpeResult(
        expected_max_sharpe=expected_max,
        z_score=z_score,
        probability=probability,
    )


def _strategy_performance(values: Sequence[float], metric: str) -> float:
    if metric == "mean":
        return _mean(values)
    if metric == "sharpe":
        mean = _mean(values)
        std = _sample_std(values)
        if std is None:
            if mean > 0:
                return math.inf
            if mean < 0:
                return -math.inf
            return 0.0
        return mean / std
    raise ValueError("performance_metric must be 'mean' or 'sharpe'")


def _average_rank(values: Sequence[float], index: int) -> float:
    """Ascending rank with worst=1, best=N and average rank for ties."""

    target = values[index]
    ordered = sorted(values)
    positions = [
        position
        for position, value in enumerate(ordered, start=1)
        if value == target
    ]
    if not positions:
        raise AssertionError("selected performance disappeared from rank set")
    return _mean([float(position) for position in positions])


def cscv_probability_of_backtest_overfitting(
    strategy_returns: Sequence[Sequence[Decimal]],
    *,
    partitions: int,
    performance_metric: str = "sharpe",
) -> PBOResult:
    """Estimate PBO using deterministic Combinatorially Symmetric CV."""

    if partitions < 2 or partitions % 2:
        raise ValueError("partitions must be a positive even number")
    rows = tuple(tuple(row) for row in strategy_returns)
    if not rows:
        raise ValueError("strategy return matrix is required")
    width = len(rows[0])
    if width < 2:
        raise ValueError("PBO requires at least two strategy variants")
    if any(len(row) != width for row in rows):
        raise ValueError("strategy return matrix must be rectangular")
    if len(rows) % partitions:
        raise ValueError("row count must divide evenly into partitions")
    partition_size = len(rows) // partitions
    if partition_size <= 0:
        raise ValueError("each partition must contain observations")

    matrix: tuple[tuple[float, ...], ...] = tuple(
        tuple(float(value) for value in row) for row in rows
    )
    if any(not math.isfinite(value) for row in matrix for value in row):
        raise ValueError("strategy returns must be finite")

    blocks = tuple(
        tuple(range(start, start + partition_size))
        for start in range(0, len(matrix), partition_size)
    )
    all_blocks = frozenset(range(partitions))
    logits: list[float] = []

    for selected in itertools.combinations(range(partitions), partitions // 2):
        is_blocks = set(selected)
        oos_blocks = all_blocks.difference(is_blocks)
        is_indices = [index for block in sorted(is_blocks) for index in blocks[block]]
        oos_indices = [index for block in sorted(oos_blocks) for index in blocks[block]]

        is_scores = [
            _strategy_performance(
                [matrix[row_index][strategy] for row_index in is_indices],
                performance_metric,
            )
            for strategy in range(width)
        ]
        best_is_score = max(is_scores)
        winner = next(
            strategy
            for strategy, score in enumerate(is_scores)
            if score == best_is_score
        )
        oos_scores = [
            _strategy_performance(
                [matrix[row_index][strategy] for row_index in oos_indices],
                performance_metric,
            )
            for strategy in range(width)
        ]
        rank = _average_rank(oos_scores, winner)
        omega = rank / (width + 1.0)
        if not 0.0 < omega < 1.0:
            raise AssertionError("CSCV relative OOS rank must be inside (0, 1)")
        logits.append(math.log(omega / (1.0 - omega)))

    negative = sum(1 for value in logits if value < 0.0)
    return PBOResult(
        combinations=len(logits),
        negative_logits=negative,
        probability=negative / len(logits),
        logits=tuple(logits),
    )


__all__ = [
    "DeflatedSharpeResult",
    "PBOResult",
    "PathObservation",
    "RobustnessReport",
    "compute_robustness_report",
    "cscv_probability_of_backtest_overfitting",
    "deflated_sharpe_probability",
]
