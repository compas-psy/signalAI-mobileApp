"""Cost-aware replay built on the immutable SAI-004 execution trace.

Execution semantics are intentionally delegated to :func:`run_backtest` and
never reimplemented here.  The second pass attaches deterministic fees,
slippage/spread and funding to the actual fill/exit timeline.  This keeps
stop-before-target, partial-fill and trailing behavior byte-for-byte owned by
the golden engine while allowing cost assumptions to evolve independently.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from .costs import CostModel
from .engine import BacktestPlan, run_backtest
from .events import BacktestEvent, EventKind, MarketBar
from .execution_model import VenueConstraints
from .result import BacktestResult


_COST_KINDS = {EventKind.FEE, EventKind.SLIPPAGE, EventKind.FUNDING}


def _scaled(amount: Decimal, share: Decimal) -> Decimal:
    if share <= 0:
        return Decimal(0)
    return amount * min(Decimal(1), share)


def run_costed_backtest(
    plan: BacktestPlan,
    bars: list[MarketBar] | tuple[MarketBar, ...],
    *,
    constraints: VenueConstraints,
    cost_model: CostModel,
    funding_interval: timedelta | None = None,
    entry_maker: bool = False,
    exit_maker: bool = False,
    evaluated_at: datetime | None = None,
    max_hold_days: int = 30,
) -> BacktestResult:
    """Run execution once, then attach an auditable point-in-time cost overlay."""

    if cost_model is None:  # type guard for dynamic callers / future promotion code
        raise ValueError("cost_model is required for cost-aware backtest")
    if funding_interval is not None and funding_interval <= timedelta(0):
        raise ValueError("funding_interval must be positive")
    if cost_model.funding_bps_per_interval > 0 and funding_interval is None:
        raise ValueError("funding_interval is required when funding cost is non-zero")

    base = run_backtest(
        plan,
        bars,
        constraints=constraints,
        evaluated_at=evaluated_at,
        max_hold_days=max_hold_days,
    )
    if base.entry_time is None or base.filled_quantity <= 0:
        return base

    risk = abs(plan.entry - plan.initial_stop)
    additions: list[tuple[datetime, int, BacktestEvent]] = []
    cost_r = Decimal(0)

    def add(
        *,
        at: datetime,
        order: int,
        kind: EventKind,
        amount: Decimal,
        detail: str,
    ) -> None:
        nonlocal cost_r
        if amount <= 0:
            return
        cost_r += amount
        additions.append(
            (
                at,
                order,
                BacktestEvent(at=at, kind=kind, amount_r=amount, detail=detail),
            )
        )

    # Attach entry friction directly after the actual fill event.
    entry_event_index = next(
        index
        for index, event in enumerate(base.events)
        if event.kind in {EventKind.ENTRY_FILL, EventKind.ENTRY_PARTIAL_FILL}
    )
    add(
        at=base.entry_time,
        order=entry_event_index * 10 + 1,
        kind=EventKind.SLIPPAGE,
        amount=cost_model.entry_friction_r(price=plan.entry, risk_per_unit=risk),
        detail=(
            f"entry slippage+half-spread: {cost_model.entry_slippage_bps}+"
            f"{cost_model.spread_bps / Decimal(2)} bps"
        ),
    )
    add(
        at=base.entry_time,
        order=entry_event_index * 10 + 2,
        kind=EventKind.FEE,
        amount=cost_model.fee_cost_r(
            price=plan.entry, risk_per_unit=risk, maker=entry_maker
        ),
        detail=f"entry {'maker' if entry_maker else 'taker'} fee",
    )

    # Charge each realised exit leg, not an idealised final close. This matters
    # for multi-target plans because position size declines through time.
    for index, event in enumerate(base.events):
        if event.kind is EventKind.TARGET:
            if event.target_index is None or event.price is None:
                continue
            share = plan.targets[event.target_index - 1].share
            friction = _scaled(
                cost_model.exit_friction_r(price=event.price, risk_per_unit=risk), share
            )
            fee = _scaled(
                cost_model.fee_cost_r(
                    price=event.price, risk_per_unit=risk, maker=exit_maker
                ),
                share,
            )
            add(
                at=event.at,
                order=index * 10 + 1,
                kind=EventKind.SLIPPAGE,
                amount=friction,
                detail=f"target {event.target_index} exit slippage+half-spread",
            )
            add(
                at=event.at,
                order=index * 10 + 2,
                kind=EventKind.FEE,
                amount=fee,
                detail=f"target {event.target_index} exit fee",
            )
        elif event.kind is EventKind.STOP and event.price is not None:
            share = (
                event.quantity / base.filled_quantity
                if event.quantity is not None and base.filled_quantity > 0
                else Decimal(1)
            )
            add(
                at=event.at,
                order=index * 10 + 1,
                kind=EventKind.SLIPPAGE,
                amount=_scaled(
                    cost_model.exit_friction_r(price=event.price, risk_per_unit=risk),
                    share,
                ),
                detail="stop exit slippage+half-spread",
            )
            add(
                at=event.at,
                order=index * 10 + 2,
                kind=EventKind.FEE,
                amount=_scaled(
                    cost_model.fee_cost_r(
                        price=event.price, risk_per_unit=risk, maker=exit_maker
                    ),
                    share,
                ),
                detail="stop exit fee",
            )

    # Funding uses the actual remaining position at each interval. At an exact
    # timestamp tie, funding precedes stop/target handling, matching SAI-004's
    # explicit funding semantics.
    if funding_interval is not None and cost_model.funding_bps_per_interval > 0:
        horizon = base.close_time
        if horizon is None:
            horizon = evaluated_at
        if horizon is None:
            horizon = bars[-1].open_time if bars else base.entry_time

        funding_at = base.entry_time + funding_interval
        while funding_at <= horizon:
            exited_share = Decimal(0)
            for event in base.events:
                if event.at >= funding_at:
                    continue
                if event.kind is EventKind.TARGET and event.target_index is not None:
                    exited_share += plan.targets[event.target_index - 1].share
                elif event.kind is EventKind.STOP:
                    exited_share = Decimal(1)
                    break
            remaining_share = max(Decimal(0), Decimal(1) - exited_share)
            amount = _scaled(
                cost_model.funding_cost_r(
                    reference_price=plan.entry,
                    risk_per_unit=risk,
                    intervals=1,
                ),
                remaining_share,
            )
            add(
                at=funding_at,
                order=-1,
                kind=EventKind.FUNDING,
                amount=amount,
                detail="funding interval charge on remaining position",
            )
            funding_at += funding_interval

    # Base engine is zero-cost here by construction. Merge by time while keeping
    # its original event sequence stable at equal timestamps.
    timeline: list[tuple[datetime, int, BacktestEvent]] = [
        (event.at, index * 10, event)
        for index, event in enumerate(base.events)
        if event.kind not in _COST_KINDS
    ]
    timeline.extend(additions)
    timeline.sort(key=lambda item: (item[0], item[1]))
    events = tuple(item[2] for item in timeline)

    return BacktestResult(
        outcome=base.outcome,
        events=events,
        filled_quantity=base.filled_quantity,
        gross_r=base.gross_r,
        cost_r=cost_r,
        net_r=base.gross_r - cost_r,
        current_stop=base.current_stop,
        targets_hit=base.targets_hit,
        entry_time=base.entry_time,
        close_time=base.close_time,
    )


__all__ = ["run_costed_backtest"]
