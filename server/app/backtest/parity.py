"""Offline parity harness for the production Paper tracker and golden backtest.

The harness does not modify either execution implementation. It feeds the same
immutable fixture through ``paper.tracker.advance`` and the SAI-004/005 golden
engine, normalises their observable outcomes, and applies the same ``CostModel``
to the paper trace for an apples-to-apples net-R comparison.

This module is measurement-only and is not imported by scanner, notification,
risk, scheduler, paper lifecycle or execution runtime paths.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from ..models import Bar, PaperTrade
from ..models.enums import Direction, PaperStatus, Timeframe
from ..paper.tracker import advance
from .costed_engine import run_costed_backtest
from .costs import CostModel
from .engine import BacktestPlan, Target
from .events import BacktestEvent, EventKind, MarketBar
from .execution_model import VenueConstraints
from .result import BacktestOutcome


_COST_KINDS = frozenset({EventKind.FEE, EventKind.SLIPPAGE, EventKind.FUNDING})


@dataclass(frozen=True, slots=True)
class ParityCase:
    name: str
    direction: Direction
    signal_available_at: datetime
    entry: Decimal
    stop: Decimal
    targets: tuple[Decimal, ...]
    quantity: Decimal
    expires_at: datetime
    bars: tuple[MarketBar, ...]
    cost_model: CostModel
    funding_interval: timedelta
    expected_execution: tuple[str, ...]
    expected_close_reason: str


@dataclass(frozen=True, slots=True)
class ParityFixture:
    cases: tuple[ParityCase, ...]


@dataclass(frozen=True, slots=True)
class ParitySideResult:
    execution_kinds: tuple[str, ...]
    entry_time: datetime | None
    close_time: datetime | None
    targets_hit: int
    current_stop: Decimal
    gross_r: Decimal
    cost_r: Decimal
    net_r: Decimal
    close_reason: str


@dataclass(frozen=True, slots=True)
class ParityResult:
    paper: ParitySideResult
    golden: ParitySideResult


def _dt(raw: str) -> datetime:
    value = datetime.fromisoformat(raw)
    if value.tzinfo is None:
        raise ValueError("parity fixture timestamps must be timezone-aware")
    return value


def _cost_model(payload: dict) -> CostModel:
    return CostModel(
        maker_fee_bps=Decimal(payload["maker_fee_bps"]),
        taker_fee_bps=Decimal(payload["taker_fee_bps"]),
        entry_slippage_bps=Decimal(payload["entry_slippage_bps"]),
        exit_slippage_bps=Decimal(payload["exit_slippage_bps"]),
        funding_bps_per_interval=Decimal(payload["funding_bps_per_interval"]),
        spread_bps=Decimal(payload["spread_bps"]),
    )


def load_parity_fixture(path: str | Path) -> ParityFixture:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    costs = _cost_model(payload["cost_model"])
    funding_interval = timedelta(hours=int(payload["funding_interval_hours"]))
    if funding_interval <= timedelta(0):
        raise ValueError("parity funding interval must be positive")

    cases: list[ParityCase] = []
    for raw in payload["cases"]:
        signal_time = _dt(raw["signal_available_at"])
        bars = tuple(
            MarketBar(
                open_time=_dt(item["open_time"]),
                open=Decimal(item["open"]),
                high=Decimal(item["high"]),
                low=Decimal(item["low"]),
                close=Decimal(item["close"]),
            )
            for item in raw["bars"]
        )
        if any(bar.open_time <= signal_time for bar in bars):
            # Production paper tracking starts from bars strictly after owner
            # activation. Keep fixtures inside the common observable domain.
            raise ValueError("parity bars must start strictly after signal/activation time")
        cases.append(
            ParityCase(
                name=str(raw["name"]),
                direction=Direction(raw["direction"]),
                signal_available_at=signal_time,
                entry=Decimal(raw["entry"]),
                stop=Decimal(raw["stop"]),
                targets=tuple(Decimal(value) for value in raw["targets"]),
                quantity=Decimal(raw["quantity"]),
                expires_at=_dt(raw["expires_at"]),
                bars=bars,
                cost_model=costs,
                funding_interval=funding_interval,
                expected_execution=tuple(raw["expected_execution"]),
                expected_close_reason=str(raw["expected_close_reason"]),
            )
        )
    return ParityFixture(cases=tuple(cases))


def _shares(case: ParityCase) -> tuple[Decimal, ...]:
    share = Decimal(1) / Decimal(len(case.targets))
    return tuple(share for _ in case.targets)


def _paper_bar(case: ParityCase, bar: MarketBar) -> Bar:
    return Bar(
        instrument_id=f"PARITY:{case.name}",
        timeframe=Timeframe.H1,
        open_time=bar.open_time,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume_units=None,
        volume_notional=None,
        open_interest=None,
        is_closed=True,
        source="parity_fixture",
        quality_flags=[],
    )


def _paper_trade(case: ParityCase) -> PaperTrade:
    shares = _shares(case)
    return PaperTrade(
        idea_id=uuid.uuid4(),
        instrument_id=f"PARITY:{case.name}",
        direction=case.direction,
        status=PaperStatus.PENDING,
        entry=case.entry,
        initial_stop=case.stop,
        tp_prices=[str(price) for price in case.targets],
        tp_shares=[str(share) for share in shares],
        current_stop=case.stop,
        tps_taken=0,
        breakeven_at=None,
        realized_r=Decimal(0),
        opened_at=case.signal_available_at,
        expires_at=case.expires_at,
        last_reconciled_at=None,
        closed_at=None,
        outcome="",
        close_reason="",
    )


def _paper_execution(case: ParityCase) -> tuple[PaperTrade, tuple[BacktestEvent, ...]]:
    trade = _paper_trade(case)
    shares = _shares(case)
    events: list[BacktestEvent] = []

    for market_bar in case.bars:
        if trade.status in (PaperStatus.CLOSED, PaperStatus.CANCELLED):
            break
        before_status = trade.status
        before_targets = trade.tps_taken
        before_stop = Decimal(trade.current_stop)
        advance(trade, [_paper_bar(case, market_bar)], now=market_bar.open_time)

        if before_status is PaperStatus.PENDING and trade.status is not PaperStatus.PENDING:
            events.append(
                BacktestEvent(
                    at=market_bar.open_time,
                    kind=EventKind.ENTRY_FILL,
                    price=case.entry,
                    quantity=case.quantity,
                    detail="paper tracker entry transition",
                )
            )

        for target_index in range(before_targets + 1, trade.tps_taken + 1):
            target = case.targets[target_index - 1]
            events.append(
                BacktestEvent(
                    at=market_bar.open_time,
                    kind=EventKind.TARGET,
                    price=target,
                    quantity=case.quantity * shares[target_index - 1],
                    target_index=target_index,
                    detail=f"paper tracker target {target_index}",
                )
            )
            if target_index == 1:
                moved_to = case.entry
            elif target_index == 2:
                moved_to = case.targets[0]
            else:
                moved_to = None
            if moved_to is not None:
                events.append(
                    BacktestEvent(
                        at=market_bar.open_time,
                        kind=EventKind.TRAILING_STOP_MOVED,
                        price=moved_to,
                        detail=f"paper tracker trailing move after target {target_index}",
                    )
                )

        if (
            trade.status is PaperStatus.CLOSED
            and trade.outcome in {"SL", "BE"}
            and before_status is not PaperStatus.CLOSED
        ):
            remaining_share = max(
                Decimal(0),
                Decimal(1) - sum(shares[: trade.tps_taken], Decimal(0)),
            )
            # If the bar stopped before any new target, current_stop is exactly
            # the stop used by production paper semantics. ``before_stop`` is
            # retained as an assertion guard against an accidental same-bar
            # trailing-stop reinterpretation.
            stop_price = Decimal(trade.current_stop)
            if trade.tps_taken == before_targets and stop_price != before_stop:
                raise AssertionError("paper stop changed on a stop-first bar")
            events.append(
                BacktestEvent(
                    at=market_bar.open_time,
                    kind=EventKind.STOP,
                    price=stop_price,
                    quantity=case.quantity * remaining_share,
                    detail="paper tracker stop transition",
                )
            )

    return trade, tuple(events)


def _paper_cost_r(
    case: ParityCase,
    events: tuple[BacktestEvent, ...],
    *,
    entry_time: datetime | None,
    close_time: datetime | None,
) -> Decimal:
    if entry_time is None:
        return Decimal(0)
    risk = abs(case.entry - case.stop)
    if risk <= 0:
        raise ValueError("parity case risk must be positive")
    shares = _shares(case)
    cost = case.cost_model.entry_friction_r(price=case.entry, risk_per_unit=risk)
    cost += case.cost_model.fee_cost_r(
        price=case.entry,
        risk_per_unit=risk,
        maker=False,
    )

    for event in events:
        if event.kind is EventKind.TARGET and event.price is not None:
            if event.target_index is None:
                raise AssertionError("paper target event missing index")
            share = shares[event.target_index - 1]
            cost += case.cost_model.exit_friction_r(
                price=event.price, risk_per_unit=risk
            ) * share
            cost += case.cost_model.fee_cost_r(
                price=event.price,
                risk_per_unit=risk,
                maker=False,
            ) * share
        elif event.kind is EventKind.STOP and event.price is not None:
            share = (
                event.quantity / case.quantity
                if event.quantity is not None and case.quantity > 0
                else Decimal(1)
            )
            cost += case.cost_model.exit_friction_r(
                price=event.price, risk_per_unit=risk
            ) * share
            cost += case.cost_model.fee_cost_r(
                price=event.price,
                risk_per_unit=risk,
                maker=False,
            ) * share

    horizon = close_time or (case.bars[-1].open_time if case.bars else entry_time)
    funding_at = entry_time + case.funding_interval
    while funding_at <= horizon:
        exited_share = Decimal(0)
        for event in events:
            # Funding is charged before any exit recorded at the same timestamp,
            # matching SAI-005 costed_engine semantics.
            if event.at >= funding_at:
                continue
            if event.kind is EventKind.TARGET and event.target_index is not None:
                exited_share += shares[event.target_index - 1]
            elif event.kind is EventKind.STOP:
                exited_share = Decimal(1)
                break
        remaining_share = max(Decimal(0), Decimal(1) - exited_share)
        cost += case.cost_model.funding_cost_r(
            reference_price=case.entry,
            risk_per_unit=risk,
            intervals=1,
        ) * remaining_share
        funding_at += case.funding_interval
    return cost


def _paper_close_reason(trade: PaperTrade) -> str:
    if trade.status is PaperStatus.CLOSED:
        if trade.outcome in {"SL", "BE"}:
            return "STOP"
        if trade.outcome == "TP":
            return "TARGETS"
        return "TIMEOUT"
    if trade.status is PaperStatus.CANCELLED:
        return "TIMEOUT"
    if trade.status is PaperStatus.OPEN:
        return "OPEN"
    return "NO_FILL"


def _paper_result(case: ParityCase) -> ParitySideResult:
    trade, events = _paper_execution(case)
    entry_event = next(
        (event for event in events if event.kind is EventKind.ENTRY_FILL), None
    )
    entry_time = entry_event.at if entry_event is not None else None
    close_time = trade.closed_at
    cost_r = _paper_cost_r(
        case,
        events,
        entry_time=entry_time,
        close_time=close_time,
    )
    gross_r = Decimal(trade.realized_r)
    return ParitySideResult(
        execution_kinds=tuple(event.kind.value for event in events),
        entry_time=entry_time,
        close_time=close_time,
        targets_hit=trade.tps_taken,
        current_stop=Decimal(trade.current_stop),
        gross_r=gross_r,
        cost_r=cost_r,
        net_r=gross_r - cost_r,
        close_reason=_paper_close_reason(trade),
    )


def _golden_close_reason(outcome: BacktestOutcome) -> str:
    if outcome is BacktestOutcome.STOP:
        return "STOP"
    if outcome is BacktestOutcome.TARGETS:
        return "TARGETS"
    if outcome in {BacktestOutcome.TIMEOUT, BacktestOutcome.REJECTED}:
        return "TIMEOUT"
    if outcome is BacktestOutcome.OPEN:
        return "OPEN"
    return "NO_FILL"


def _golden_result(case: ParityCase) -> ParitySideResult:
    shares = _shares(case)
    plan = BacktestPlan(
        direction=case.direction,
        signal_available_at=case.signal_available_at,
        entry=case.entry,
        initial_stop=case.stop,
        targets=tuple(
            Target(price=price, share=share)
            for price, share in zip(case.targets, shares)
        ),
        requested_quantity=case.quantity,
        expires_at=case.expires_at,
    )
    result = run_costed_backtest(
        plan,
        list(case.bars),
        constraints=VenueConstraints(
            quantity_step=case.quantity,
            min_quantity=case.quantity,
        ),
        cost_model=case.cost_model,
        funding_interval=case.funding_interval,
    )
    execution = tuple(
        event.kind.value
        for event in result.events
        if event.kind not in _COST_KINDS and event.kind is not EventKind.SIGNAL_AVAILABLE
    )
    return ParitySideResult(
        execution_kinds=execution,
        entry_time=result.entry_time,
        close_time=result.close_time,
        targets_hit=result.targets_hit,
        current_stop=result.current_stop,
        gross_r=result.gross_r,
        cost_r=result.cost_r,
        net_r=result.net_r,
        close_reason=_golden_close_reason(result.outcome),
    )


def run_parity_case(case: ParityCase) -> ParityResult:
    return ParityResult(paper=_paper_result(case), golden=_golden_result(case))


__all__ = [
    "ParityCase",
    "ParityFixture",
    "ParityResult",
    "ParitySideResult",
    "load_parity_fixture",
    "run_parity_case",
]
