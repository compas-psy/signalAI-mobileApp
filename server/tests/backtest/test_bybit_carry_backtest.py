from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.bybit_carry_backtest import (
    CarryReplayGate,
    calculate_carry_outcome,
    replay_carry_oos,
)
from app.market.derivatives import CryptoCarryMarketFacts, FundingObservation
from app.models.enums import Direction


def _funding(at: datetime, rate: str) -> FundingObservation:
    return FundingObservation(
        rate=Decimal(rate),
        settled_at=at,
        tradable_at=at,
        source="bybit_snapshot",
    )


def test_short_positive_funding_includes_realized_basis_and_costs() -> None:
    entry_at = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    exit_at = entry_at + timedelta(hours=16)
    outcome = calculate_carry_outcome(
        direction=Direction.SHORT,
        entry_at=entry_at,
        exit_at=exit_at,
        entry_mark=Decimal("100.20"),
        entry_index=Decimal("100.00"),
        exit_mark=Decimal("100.10"),
        exit_index=Decimal("100.00"),
        funding_history=(
            _funding(entry_at + timedelta(hours=8), "0.001"),
            _funding(entry_at + timedelta(hours=16), "0.002"),
        ),
        round_trip_cost_bps=Decimal("5"),
        hedge_carry_bps_per_interval=Decimal("2"),
        funding_interval_minutes=480,
    )

    assert outcome.funding_bps == Decimal("30")
    assert outcome.basis_bps == Decimal("10")
    assert outcome.cost_bps == Decimal("9")
    assert outcome.net_bps == Decimal("31")
    assert outcome.funding_events == 2


def test_long_negative_funding_uses_opposite_basis_direction() -> None:
    entry_at = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    exit_at = entry_at + timedelta(hours=16)
    outcome = calculate_carry_outcome(
        direction=Direction.LONG,
        entry_at=entry_at,
        exit_at=exit_at,
        entry_mark=Decimal("99.90"),
        entry_index=Decimal("100.00"),
        exit_mark=Decimal("99.95"),
        exit_index=Decimal("100.00"),
        funding_history=(
            _funding(entry_at + timedelta(hours=8), "-0.001"),
            _funding(entry_at + timedelta(hours=16), "-0.002"),
        ),
        round_trip_cost_bps=Decimal("5"),
        hedge_carry_bps_per_interval=Decimal("2"),
        funding_interval_minutes=480,
    )

    assert outcome.funding_bps == Decimal("30")
    assert outcome.basis_bps == Decimal("5")
    assert outcome.cost_bps == Decimal("9")
    assert outcome.net_bps == Decimal("26")


def test_funding_outside_holding_window_is_not_counted() -> None:
    entry_at = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    exit_at = entry_at + timedelta(hours=8)
    outcome = calculate_carry_outcome(
        direction=Direction.SHORT,
        entry_at=entry_at,
        exit_at=exit_at,
        entry_mark=Decimal("100"),
        entry_index=Decimal("100"),
        exit_mark=Decimal("100"),
        exit_index=Decimal("100"),
        funding_history=(
            _funding(entry_at, "0.010"),
            _funding(exit_at, "0.001"),
            _funding(exit_at + timedelta(hours=8), "0.010"),
        ),
        round_trip_cost_bps=Decimal("0"),
        hedge_carry_bps_per_interval=Decimal("0"),
        funding_interval_minutes=480,
    )

    assert outcome.funding_bps == Decimal("10")
    assert outcome.funding_events == 1


def test_replay_uses_pit_history_and_reports_carry_metric_space() -> None:
    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    funding = tuple(
        _funding(start + timedelta(hours=8 * index), "0.002")
        for index in range(13)
    )
    facts: list[CryptoCarryMarketFacts] = []
    for index in (9, 12):
        at = start + timedelta(hours=8 * index)
        facts.append(
            CryptoCarryMarketFacts(
                instrument_id="BTCUSDT",
                mark_price=Decimal("100"),
                index_price=Decimal("100"),
                current_funding_rate=Decimal("0.002"),
                funding_interval_minutes=480,
                funding_history=funding[: index + 1],
                observed_at=at,
                tradable_at=at,
                source="bybit_snapshot",
            )
        )

    report = replay_carry_oos(
        facts=tuple(facts),
        execution_cost_bps=Decimal("11"),
        hedge_carry_bps_per_interval=Decimal("2"),
        funding_uncertainty_bps_per_interval=Decimal("2"),
        gate=CarryReplayGate(
            min_trades=1,
            min_profit_factor=Decimal("1.2"),
            min_expectancy_bps=Decimal("1"),
            max_top5_contribution=Decimal("1"),
        ),
    )

    assert report.metric_space == "CARRY_BPS"
    assert len(report.outcomes) == 1
    assert report.outcomes[0].funding_bps == Decimal("60")
    # Realized cost = 11 bps execution + 3 settled funding intervals × 2 bps
    # hedge carry. The predictive funding-uncertainty haircut is deliberately
    # not charged to realized P&L.
    assert report.outcomes[0].cost_bps == Decimal("17")
    assert report.outcomes[0].net_bps == Decimal("43")
    assert report.expectancy_bps == Decimal("43")
    assert report.profit_factor == Decimal("Infinity")
    assert report.gate_passed is True
    assert report.gate_reasons == ()
