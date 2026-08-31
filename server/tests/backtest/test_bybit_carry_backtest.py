from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.market.derivatives import FundingObservation
from app.models.enums import Direction
from app.backtest.bybit_carry_backtest import calculate_carry_outcome


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
