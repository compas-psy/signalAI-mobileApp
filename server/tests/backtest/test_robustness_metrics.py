from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtest.robustness import (
    PathObservation,
    compute_robustness_report,
    cscv_probability_of_backtest_overfitting,
    deflated_sharpe_probability,
)


BASE = datetime(2026, 8, 18, tzinfo=UTC)


def observation(
    index: int,
    *,
    gross: str,
    net: str,
    turnover: str,
    mae: str,
    mfe: str,
    regime: str,
    probability: str | None = None,
    positive: bool | None = None,
) -> PathObservation:
    return PathObservation(
        at=BASE + timedelta(days=index),
        gross_return=Decimal(gross),
        net_return=Decimal(net),
        turnover=Decimal(turnover),
        mae=Decimal(mae),
        mfe=Decimal(mfe),
        regime=regime,
        predicted_positive_probability=(
            Decimal(probability) if probability is not None else None
        ),
        positive_outcome=positive,
    )


def test_report_keeps_profitability_risk_cost_and_path_metrics_together():
    rows = (
        observation(0, gross="0.025", net="0.020", turnover="0.40", mae="-0.010", mfe="0.030", regime="TREND", probability="0.7", positive=True),
        observation(1, gross="-0.005", net="-0.010", turnover="0.20", mae="-0.020", mfe="0.010", regime="RANGE", probability="0.2", positive=False),
        observation(2, gross="0.035", net="0.030", turnover="0.50", mae="-0.005", mfe="0.040", regime="TREND", probability="0.8", positive=True),
        observation(3, gross="-0.015", net="-0.020", turnover="0.30", mae="-0.030", mfe="0.005", regime="RANGE", probability="0.3", positive=False),
        observation(4, gross="0.015", net="0.010", turnover="0.25", mae="-0.010", mfe="0.020", regime="RANGE", probability="0.6", positive=True),
    )

    report = compute_robustness_report(rows, periods_per_year=Decimal("252"))

    assert report.observations == 5
    assert report.gross_total_return > report.net_total_return
    assert report.expectancy == pytest.approx(0.006)
    assert report.profit_factor == pytest.approx(2.0)
    assert report.win_rate == pytest.approx(0.6)
    assert report.payoff_ratio == pytest.approx(4 / 3)
    assert report.turnover == pytest.approx(1.65)
    assert report.cost_ratio == pytest.approx(0.025 / 0.095)
    assert report.average_mae == pytest.approx(-0.015)
    assert report.average_mfe == pytest.approx(0.021)
    assert report.tail_loss_5pct == pytest.approx(-0.020)
    assert report.longest_loss_streak == 1
    assert report.max_drawdown > 0
    assert report.sharpe is not None
    assert report.sortino is not None
    assert report.calmar is not None
    assert report.pnl_concentration_hhi > 0
    assert report.brier_score == pytest.approx(0.084)
    assert report.regime_expectancy == {
        "RANGE": pytest.approx(-0.02 / 3),
        "TREND": pytest.approx(0.025),
    }
    assert report.worst_regime_expectancy == pytest.approx(-0.02 / 3)


def test_report_rejects_non_chronological_or_inconsistent_cost_rows():
    good = observation(0, gross="0.02", net="0.01", turnover="0", mae="-0.01", mfe="0.02", regime="TREND")
    earlier = PathObservation(
        at=BASE - timedelta(days=1),
        gross_return=Decimal("0.02"),
        net_return=Decimal("0.01"),
        turnover=Decimal("0"),
        mae=Decimal("-0.01"),
        mfe=Decimal("0.02"),
        regime="TREND",
    )
    with pytest.raises(ValueError, match="chronological"):
        compute_robustness_report((good, earlier), periods_per_year=Decimal("252"))

    with pytest.raises(ValueError, match="net_return cannot exceed gross_return"):
        observation(1, gross="0.01", net="0.02", turnover="0", mae="-0.01", mfe="0.02", regime="TREND")


def test_deflated_sharpe_matches_bailey_lopez_de_prado_equation_two_reference_case():
    # Bailey & Lopez de Prado (2014), Eq. 2. The trial standard deviation and
    # N feed the expected-maximum Sharpe threshold from Eq. 1 / Appendix snippet.
    result = deflated_sharpe_probability(
        observed_sharpe=Decimal("0.9"),
        sample_length=100,
        skewness=Decimal("0"),
        kurtosis=Decimal("3"),
        independent_trials=100,
        trial_sharpe_std=Decimal("0.4"),
    )

    assert result.expected_max_sharpe == pytest.approx(1.0122411572804568)
    assert result.z_score == pytest.approx(-0.9421749886118892)
    assert result.probability == pytest.approx(0.17305152847539157)


def test_deflated_sharpe_penalises_more_independent_trials():
    few = deflated_sharpe_probability(
        observed_sharpe=Decimal("1.1"),
        sample_length=100,
        skewness=Decimal("0"),
        kurtosis=Decimal("3"),
        independent_trials=10,
        trial_sharpe_std=Decimal("0.4"),
    )
    many = deflated_sharpe_probability(
        observed_sharpe=Decimal("1.1"),
        sample_length=100,
        skewness=Decimal("0"),
        kurtosis=Decimal("3"),
        independent_trials=100,
        trial_sharpe_std=Decimal("0.4"),
    )

    assert many.expected_max_sharpe > few.expected_max_sharpe
    assert many.probability < few.probability


def test_cscv_pbo_matches_a_hand_checkable_best_is_worst_oos_case():
    # Four equal time partitions, two strategies. A dominates partitions 1-2,
    # B dominates 3-4. Of the six CSCV IS combinations, two pick a strategy
    # that is strictly worst OOS and four are ties (logit == 0): PBO = 2/6.
    matrix = (
        (Decimal("1"), Decimal("-1")),
        (Decimal("1"), Decimal("-1")),
        (Decimal("1"), Decimal("-1")),
        (Decimal("1"), Decimal("-1")),
        (Decimal("-1"), Decimal("1")),
        (Decimal("-1"), Decimal("1")),
        (Decimal("-1"), Decimal("1")),
        (Decimal("-1"), Decimal("1")),
    )

    result = cscv_probability_of_backtest_overfitting(
        matrix,
        partitions=4,
        performance_metric="mean",
    )

    assert result.combinations == 6
    assert result.negative_logits == 2
    assert result.probability == pytest.approx(1 / 3)
    assert len(result.logits) == 6


def test_cscv_requires_synchronous_rectangular_even_partitions():
    with pytest.raises(ValueError, match="even"):
        cscv_probability_of_backtest_overfitting(
            ((Decimal("1"), Decimal("2")),) * 6,
            partitions=3,
        )
    with pytest.raises(ValueError, match="divide"):
        cscv_probability_of_backtest_overfitting(
            ((Decimal("1"), Decimal("2")),) * 7,
            partitions=4,
        )
    with pytest.raises(ValueError, match="rectangular"):
        cscv_probability_of_backtest_overfitting(
            (
                (Decimal("1"), Decimal("2")),
                (Decimal("1"),),
                (Decimal("1"), Decimal("2")),
                (Decimal("1"), Decimal("2")),
            ),
            partitions=4,
        )
