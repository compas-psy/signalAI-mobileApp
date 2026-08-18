"""Deterministic event-driven golden backtest.

The engine mirrors the already-shipped paper semantics where they exist:
entry is tested on the execution bar, that same bar can immediately stop the
trade, and within one OHLC bar the stop is checked before targets.  Trailing
changes happen only after a target and therefore affect following bars, never
retroactively the bar that created the new stop.

This module is offline measurement code.  It is not imported by the production
scanner or paper tracker and cannot gate live signal generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from ..models.enums import Direction
from .costs import ExplicitCostSchedule, ZERO_COSTS
from .events import BacktestEvent, EventKind, MarketBar
from .execution_model import (
    VenueConstraints,
    deterministic_fill_quantity,
    rejection_reason,
)
from .result import BacktestOutcome, BacktestResult


@dataclass(frozen=True, slots=True)
class Target:
    price: Decimal
    share: Decimal

    def __post_init__(self) -> None:
        if self.share <= 0:
            raise ValueError("target share must be positive")


@dataclass(frozen=True, slots=True)
class BacktestPlan:
    direction: Direction
    signal_available_at: datetime
    entry: Decimal
    initial_stop: Decimal
    targets: tuple[Target, ...]
    requested_quantity: Decimal
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.signal_available_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("backtest plan times must be timezone-aware")
        if self.expires_at <= self.signal_available_at:
            raise ValueError("expires_at must be after signal availability")
        if self.requested_quantity <= 0:
            raise ValueError("requested_quantity must be positive")
        if not self.targets:
            raise ValueError("at least one target is required")
        if self.direction is Direction.LONG:
            if self.initial_stop >= self.entry:
                raise ValueError("long stop must be below entry")
            if any(target.price <= self.entry for target in self.targets):
                raise ValueError("long targets must be above entry")
            if list(target.price for target in self.targets) != sorted(
                target.price for target in self.targets
            ):
                raise ValueError("long targets must be ordered away from entry")
        else:
            if self.initial_stop <= self.entry:
                raise ValueError("short stop must be above entry")
            if any(target.price >= self.entry for target in self.targets):
                raise ValueError("short targets must be below entry")
            if list(target.price for target in self.targets) != sorted(
                (target.price for target in self.targets), reverse=True
            ):
                raise ValueError("short targets must be ordered away from entry")
        total_share = sum((target.share for target in self.targets), Decimal(0))
        if total_share > Decimal("1.0000000001"):
            raise ValueError("target shares must not exceed the whole position")


def _result(
    *,
    outcome: BacktestOutcome,
    events: list[BacktestEvent],
    filled_quantity: Decimal,
    gross_r: Decimal,
    cost_r: Decimal,
    current_stop: Decimal,
    targets_hit: int,
    entry_time: datetime | None,
    close_time: datetime | None,
) -> BacktestResult:
    return BacktestResult(
        outcome=outcome,
        events=tuple(events),
        filled_quantity=filled_quantity,
        gross_r=gross_r,
        cost_r=cost_r,
        net_r=gross_r - cost_r,
        current_stop=current_stop,
        targets_hit=targets_hit,
        entry_time=entry_time,
        close_time=close_time,
    )


def run_backtest(
    plan: BacktestPlan,
    bars: list[MarketBar] | tuple[MarketBar, ...],
    *,
    constraints: VenueConstraints,
    costs: ExplicitCostSchedule = ZERO_COSTS,
    evaluated_at: datetime | None = None,
    max_hold_days: int = 30,
) -> BacktestResult:
    """Replay one immutable plan through closed bars in chronological order."""

    if max_hold_days <= 0:
        raise ValueError("max_hold_days must be positive")
    if evaluated_at is not None and evaluated_at.tzinfo is None:
        raise ValueError("evaluated_at must be timezone-aware")
    bar_times = [bar.open_time for bar in bars]
    if bar_times != sorted(bar_times):
        raise ValueError("backtest bars must be ordered chronologically")
    if len(bar_times) != len(set(bar_times)):
        raise ValueError("backtest bars must not duplicate timestamps")

    events: list[BacktestEvent] = [
        BacktestEvent(plan.signal_available_at, EventKind.SIGNAL_AVAILABLE)
    ]
    current_stop = plan.initial_stop
    filled_quantity = Decimal(0)
    gross_r = Decimal(0)
    cost_r = Decimal(0)
    targets_hit = 0
    entry_time: datetime | None = None
    close_time: datetime | None = None
    opened = False
    funding_index = 0

    rejection = rejection_reason(
        direction=plan.direction,
        entry=plan.entry,
        stop=plan.initial_stop,
        requested_quantity=plan.requested_quantity,
        constraints=constraints,
    )
    if rejection is not None:
        events.append(
            BacktestEvent(
                plan.signal_available_at,
                EventKind.ORDER_REJECTED,
                detail=rejection,
            )
        )
        return _result(
            outcome=BacktestOutcome.REJECTED,
            events=events,
            filled_quantity=filled_quantity,
            gross_r=gross_r,
            cost_r=cost_r,
            current_stop=current_stop,
            targets_hit=targets_hit,
            entry_time=None,
            close_time=plan.signal_available_at,
        )

    risk = abs(plan.entry - plan.initial_stop)

    def r_at(price: Decimal) -> Decimal:
        move = (
            price - plan.entry
            if plan.direction is Direction.LONG
            else plan.entry - price
        )
        return move / risk

    def add_cost(at: datetime, kind: EventKind, amount: Decimal, detail: str) -> None:
        nonlocal cost_r
        if amount <= 0:
            return
        cost_r += amount
        events.append(
            BacktestEvent(at, kind, amount_r=amount, detail=detail)
        )

    def add_entry_costs(at: datetime) -> None:
        add_cost(at, EventKind.SLIPPAGE, costs.entry_slippage_r, "entry slippage")
        add_cost(at, EventKind.FEE, costs.entry_fee_r, "entry fee")

    def add_exit_costs(at: datetime) -> None:
        add_cost(at, EventKind.SLIPPAGE, costs.exit_slippage_r, "exit slippage")
        add_cost(at, EventKind.FEE, costs.exit_fee_r, "exit fee")

    def apply_funding_through(at: datetime) -> None:
        nonlocal funding_index, cost_r
        if not opened or entry_time is None:
            return
        while funding_index < len(costs.funding):
            charge = costs.funding[funding_index]
            if charge.at > at:
                break
            funding_index += 1
            if charge.at <= entry_time:
                continue
            cost_r += charge.amount_r
            if charge.amount_r > 0:
                events.append(
                    BacktestEvent(
                        charge.at,
                        EventKind.FUNDING,
                        amount_r=charge.amount_r,
                        detail="explicit funding charge",
                    )
                )

    for bar in bars:
        # A bar that started before the signal existed contains an unknowable
        # pre-signal path and is never eligible for execution.
        if bar.open_time < plan.signal_available_at:
            continue

        if not opened and bar.open_time >= plan.expires_at:
            events.append(
                BacktestEvent(plan.expires_at, EventKind.NO_FILL, detail="entry not filled")
            )
            events.append(
                BacktestEvent(plan.expires_at, EventKind.TIMEOUT, detail="entry expired")
            )
            return _result(
                outcome=BacktestOutcome.TIMEOUT,
                events=events,
                filled_quantity=filled_quantity,
                gross_r=gross_r,
                cost_r=cost_r,
                current_stop=current_stop,
                targets_hit=targets_hit,
                entry_time=None,
                close_time=plan.expires_at,
            )

        if not opened:
            if not (bar.low <= plan.entry <= bar.high):
                continue
            fill_quantity = deterministic_fill_quantity(
                plan.requested_quantity, constraints
            )
            if fill_quantity <= 0:
                events.append(
                    BacktestEvent(
                        bar.open_time,
                        EventKind.NO_FILL,
                        price=plan.entry,
                        detail="liquidity cap below venue minimum",
                    )
                )
                continue
            filled_quantity = fill_quantity
            opened = True
            entry_time = bar.open_time
            kind = (
                EventKind.ENTRY_FILL
                if fill_quantity == plan.requested_quantity
                else EventKind.ENTRY_PARTIAL_FILL
            )
            events.append(
                BacktestEvent(
                    bar.open_time,
                    kind,
                    price=plan.entry,
                    quantity=fill_quantity,
                    detail=(
                        "full deterministic fill"
                        if kind is EventKind.ENTRY_FILL
                        else "partial deterministic fill; remainder cancelled"
                    ),
                )
            )
            add_entry_costs(bar.open_time)

        apply_funding_through(bar.open_time)

        long = plan.direction is Direction.LONG
        stop_hit = bar.low <= current_stop if long else bar.high >= current_stop
        if stop_hit:
            taken_share = sum(
                (target.share for target in plan.targets[:targets_hit]), Decimal(0)
            )
            remaining_share = max(Decimal(0), Decimal(1) - taken_share)
            gross_r += r_at(current_stop) * remaining_share
            events.append(
                BacktestEvent(
                    bar.open_time,
                    EventKind.STOP,
                    price=current_stop,
                    quantity=filled_quantity * remaining_share,
                    detail="stop checked before targets inside the OHLC bar",
                )
            )
            add_exit_costs(bar.open_time)
            close_time = bar.open_time
            return _result(
                outcome=BacktestOutcome.STOP,
                events=events,
                filled_quantity=filled_quantity,
                gross_r=gross_r,
                cost_r=cost_r,
                current_stop=current_stop,
                targets_hit=targets_hit,
                entry_time=entry_time,
                close_time=close_time,
            )

        while targets_hit < len(plan.targets):
            target = plan.targets[targets_hit]
            reached = bar.high >= target.price if long else bar.low <= target.price
            if not reached:
                break
            gross_r += r_at(target.price) * target.share
            targets_hit += 1
            events.append(
                BacktestEvent(
                    bar.open_time,
                    EventKind.TARGET,
                    price=target.price,
                    quantity=filled_quantity * target.share,
                    target_index=targets_hit,
                    detail=f"target {targets_hit} reached",
                )
            )

            if targets_hit == 1:
                current_stop = plan.entry
                events.append(
                    BacktestEvent(
                        bar.open_time,
                        EventKind.TRAILING_STOP_MOVED,
                        price=current_stop,
                        detail="stop moved to breakeven after TP1",
                    )
                )
            elif targets_hit == 2:
                current_stop = plan.targets[0].price
                events.append(
                    BacktestEvent(
                        bar.open_time,
                        EventKind.TRAILING_STOP_MOVED,
                        price=current_stop,
                        detail="stop moved to TP1 after TP2",
                    )
                )

        if targets_hit >= len(plan.targets):
            add_exit_costs(bar.open_time)
            close_time = bar.open_time
            return _result(
                outcome=BacktestOutcome.TARGETS,
                events=events,
                filled_quantity=filled_quantity,
                gross_r=gross_r,
                cost_r=cost_r,
                current_stop=current_stop,
                targets_hit=targets_hit,
                entry_time=entry_time,
                close_time=close_time,
            )

    moment = evaluated_at or (bar_times[-1] if bar_times else plan.signal_available_at)
    if moment < plan.signal_available_at:
        raise ValueError("evaluated_at cannot precede signal availability")

    if not opened:
        if moment >= plan.expires_at:
            events.append(
                BacktestEvent(plan.expires_at, EventKind.NO_FILL, detail="entry not filled")
            )
            events.append(
                BacktestEvent(plan.expires_at, EventKind.TIMEOUT, detail="entry expired")
            )
            outcome = BacktestOutcome.TIMEOUT
            close_time = plan.expires_at
        else:
            events.append(
                BacktestEvent(moment, EventKind.NO_FILL, detail="entry not touched yet")
            )
            outcome = BacktestOutcome.NO_FILL
        return _result(
            outcome=outcome,
            events=events,
            filled_quantity=filled_quantity,
            gross_r=gross_r,
            cost_r=cost_r,
            current_stop=current_stop,
            targets_hit=targets_hit,
            entry_time=None,
            close_time=close_time,
        )

    apply_funding_through(moment)
    if entry_time is not None and moment - entry_time >= timedelta(days=max_hold_days):
        events.append(
            BacktestEvent(
                moment,
                EventKind.TIMEOUT,
                detail=f"open position exceeded {max_hold_days} calendar days",
            )
        )
        return _result(
            outcome=BacktestOutcome.TIMEOUT,
            events=events,
            filled_quantity=filled_quantity,
            gross_r=gross_r,
            cost_r=cost_r,
            current_stop=current_stop,
            targets_hit=targets_hit,
            entry_time=entry_time,
            close_time=moment,
        )

    return _result(
        outcome=BacktestOutcome.OPEN,
        events=events,
        filled_quantity=filled_quantity,
        gross_r=gross_r,
        cost_r=cost_r,
        current_stop=current_stop,
        targets_hit=targets_hit,
        entry_time=entry_time,
        close_time=None,
    )


__all__ = ["BacktestPlan", "Target", "run_backtest"]
