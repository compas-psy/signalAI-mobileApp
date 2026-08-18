from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtest.costs import ExplicitCostSchedule, FundingCharge
from app.backtest.engine import BacktestPlan, Target, run_backtest
from app.backtest.events import EventKind, MarketBar
from app.backtest.execution_model import VenueConstraints
from app.backtest.result import BacktestOutcome
from app.models.enums import Direction


FIXTURE = Path(__file__).parent / "fixtures" / "paper_semantics.json"
BASE = datetime(2026, 8, 18, 9, tzinfo=UTC)


def bar(hour: int, *, o: str, h: str, low: str, c: str) -> MarketBar:
    return MarketBar(
        open_time=BASE + timedelta(hours=hour),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
    )


def plan(
    *,
    direction: Direction = Direction.LONG,
    signal_at: datetime = BASE,
    entry: str = "100",
    stop: str = "95",
    targets: tuple[Target, ...] = (
        Target(Decimal("110"), Decimal("0.5")),
        Target(Decimal("120"), Decimal("0.5")),
    ),
    quantity: str = "2",
    expires_at: datetime | None = None,
) -> BacktestPlan:
    return BacktestPlan(
        direction=direction,
        signal_available_at=signal_at,
        entry=Decimal(entry),
        initial_stop=Decimal(stop),
        targets=targets,
        requested_quantity=Decimal(quantity),
        expires_at=expires_at or signal_at + timedelta(days=5),
    )


def default_constraints(**overrides) -> VenueConstraints:
    values = {
        "quantity_step": Decimal("1"),
        "min_quantity": Decimal("1"),
    }
    values.update(overrides)
    return VenueConstraints(**values)


def test_signal_availability_prevents_using_an_earlier_bar():
    p = plan(signal_at=BASE + timedelta(hours=1))
    bars = [
        bar(0, o="100", h="110", low="94", c="105"),
        bar(1, o="105", h="106", low="101", c="103"),
    ]

    result = run_backtest(p, bars, constraints=default_constraints())

    assert result.outcome is BacktestOutcome.OPEN
    assert [event.kind for event in result.events] == [
        EventKind.SIGNAL_AVAILABLE,
        EventKind.ENTRY_FILL,
    ]
    assert result.entry_time == bars[1].open_time


def test_same_bar_entry_can_stop_and_stop_precedes_target():
    p = plan()
    execution_bar = bar(1, o="100", h="115", low="94", c="105")

    result = run_backtest(p, [execution_bar], constraints=default_constraints())

    assert result.outcome is BacktestOutcome.STOP
    assert [event.kind for event in result.events] == [
        EventKind.SIGNAL_AVAILABLE,
        EventKind.ENTRY_FILL,
        EventKind.STOP,
    ]
    assert result.gross_r == Decimal("-1")


def test_targets_move_trailing_stop_only_for_following_bars():
    p = plan(
        targets=(
            Target(Decimal("110"), Decimal("0.4")),
            Target(Decimal("120"), Decimal("0.3")),
            Target(Decimal("130"), Decimal("0.3")),
        )
    )
    bars = [
        bar(1, o="100", h="111", low="99", c="110"),
        bar(2, o="111", h="121", low="109", c="120"),
        bar(3, o="112", h="115", low="109", c="110"),
    ]

    result = run_backtest(p, bars, constraints=default_constraints())

    assert result.outcome is BacktestOutcome.STOP
    assert result.targets_hit == 2
    assert result.current_stop == Decimal("110")
    assert [event.kind for event in result.events] == [
        EventKind.SIGNAL_AVAILABLE,
        EventKind.ENTRY_FILL,
        EventKind.TARGET,
        EventKind.TRAILING_STOP_MOVED,
        EventKind.TARGET,
        EventKind.TRAILING_STOP_MOVED,
        EventKind.STOP,
    ]
    assert result.gross_r == Decimal("1.8")


def test_entry_not_touched_records_no_fill_then_calendar_timeout():
    p = plan(expires_at=BASE + timedelta(hours=2))
    bars = [bar(1, o="110", h="112", low="105", c="111")]

    result = run_backtest(
        p,
        bars,
        constraints=default_constraints(),
        evaluated_at=BASE + timedelta(hours=3),
    )

    assert result.outcome is BacktestOutcome.TIMEOUT
    assert [event.kind for event in result.events] == [
        EventKind.SIGNAL_AVAILABLE,
        EventKind.NO_FILL,
        EventKind.TIMEOUT,
    ]
    assert result.filled_quantity == 0


def test_explicit_liquidity_cap_produces_deterministic_partial_fill():
    p = plan(quantity="5")
    constraints = default_constraints(max_fill_quantity=Decimal("2"))

    result = run_backtest(
        p,
        [bar(1, o="100", h="105", low="99", c="103")],
        constraints=constraints,
    )

    assert result.outcome is BacktestOutcome.OPEN
    assert result.filled_quantity == Decimal("2")
    fill = next(event for event in result.events if event.kind is EventKind.ENTRY_PARTIAL_FILL)
    assert fill.quantity == Decimal("2")


@pytest.mark.parametrize(
    ("quantity", "step", "minimum", "reason"),
    [
        ("0.5", "0.1", "1", "below venue minimum"),
        ("1.5", "1", "1", "not aligned"),
    ],
)
def test_venue_minimum_and_step_reject_invalid_plan(
    quantity: str, step: str, minimum: str, reason: str
):
    result = run_backtest(
        plan(quantity=quantity),
        [bar(1, o="100", h="101", low="99", c="100")],
        constraints=VenueConstraints(
            quantity_step=Decimal(step),
            min_quantity=Decimal(minimum),
        ),
    )

    assert result.outcome is BacktestOutcome.REJECTED
    assert result.events[-1].kind is EventKind.ORDER_REJECTED
    assert reason in result.events[-1].detail


def test_liquidation_constraint_rejects_stop_that_cannot_protect_position():
    result = run_backtest(
        plan(stop="95"),
        [],
        constraints=default_constraints(liquidation_price=Decimal("97")),
    )

    assert result.outcome is BacktestOutcome.REJECTED
    assert result.events[-1].kind is EventKind.ORDER_REJECTED
    assert "liquidation" in result.events[-1].detail


def test_explicit_cost_hooks_emit_fee_slippage_funding_and_net_r():
    costs = ExplicitCostSchedule(
        entry_fee_r=Decimal("0.01"),
        entry_slippage_r=Decimal("0.02"),
        exit_fee_r=Decimal("0.01"),
        exit_slippage_r=Decimal("0.03"),
        funding=(FundingCharge(BASE + timedelta(hours=2), Decimal("0.04")),),
    )
    p = plan(
        targets=(Target(Decimal("110"), Decimal("1")),),
        expires_at=BASE + timedelta(days=1),
    )
    bars = [
        bar(1, o="100", h="105", low="99", c="104"),
        bar(3, o="108", h="111", low="107", c="110"),
    ]

    result = run_backtest(
        p,
        bars,
        constraints=default_constraints(),
        costs=costs,
    )

    assert result.outcome is BacktestOutcome.TARGETS
    assert result.gross_r == Decimal("2")
    assert result.cost_r == Decimal("0.11")
    assert result.net_r == Decimal("1.89")
    assert [event.kind for event in result.events] == [
        EventKind.SIGNAL_AVAILABLE,
        EventKind.ENTRY_FILL,
        EventKind.SLIPPAGE,
        EventKind.FEE,
        EventKind.FUNDING,
        EventKind.TARGET,
        EventKind.SLIPPAGE,
        EventKind.FEE,
    ]


def test_first_golden_fixtures_match_production_paper_event_order():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    for case in fixture["cases"]:
        p = BacktestPlan(
            direction=Direction(case["direction"]),
            signal_available_at=datetime.fromisoformat(case["signal_available_at"]),
            entry=Decimal(case["entry"]),
            initial_stop=Decimal(case["stop"]),
            targets=tuple(
                Target(Decimal(target["price"]), Decimal(target["share"]))
                for target in case["targets"]
            ),
            requested_quantity=Decimal(case["quantity"]),
            expires_at=datetime.fromisoformat(case["expires_at"]),
        )
        bars = [
            MarketBar(
                open_time=datetime.fromisoformat(item["open_time"]),
                open=Decimal(item["open"]),
                high=Decimal(item["high"]),
                low=Decimal(item["low"]),
                close=Decimal(item["close"]),
            )
            for item in case["bars"]
        ]

        result = run_backtest(p, bars, constraints=default_constraints())

        assert [event.kind.value for event in result.events] == case["expected_kinds"], case["name"]
        assert result.outcome.value == case["expected_outcome"], case["name"]
