"""Immutable point-in-time replay for the hedged crypto carry challenger.

Carry is deliberately measured in basis points, not directional R.  Candidate
selection reuses the live ``crypto_carry_v1`` rules from facts that were known
at each funding settlement.  The realised label uses the next three settled
funding cashflows plus mark/index hedge movement, then subtracts the approved
execution and hedge-carry costs.  The live funding-uncertainty haircut is a
selection safeguard and is not charged a second time to realised P&L.
"""

from __future__ import annotations

import statistics
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from math import sqrt
from typing import Sequence

from sqlalchemy.orm import Session

from ..config import EngineConfig
from ..datasets.snapshots import ResolvedDataset
from ..market.derivatives import CryptoCarryMarketFacts, FundingObservation
from ..models import BacktestRun
from ..models.enums import Direction
from ..strategies.crypto_carry_v1 import (
    MIN_FUNDING_OBSERVATIONS,
    MIN_NET_CARRY_BPS,
    TARGET_FUNDING_INTERVALS,
    evaluate_crypto_carry_v1,
)
from ..version import ENGINE_VERSION
from .bybit_dataset import DATA_READY
from .walk_forward import TimedSample, WalkForwardConfig, purged_walk_forward

_METRIC_SPACE = "CARRY_BPS"
_OUTCOME_METRIC = "hedged_realized_carry_bps_v1"
_LABEL_PREFIX = "bybit-carry-backtest-v1"
_HISTORY_LIMIT = 24


@dataclass(frozen=True, slots=True)
class _FundingFact:
    rate: Decimal
    observed_at: datetime
    tradable_at: datetime


@dataclass(frozen=True, slots=True)
class _PriceFact:
    price: Decimal
    observed_at: datetime
    tradable_at: datetime


@dataclass(frozen=True, slots=True)
class _CarryTrade:
    decision_at: datetime
    maturity_at: datetime
    direction: Direction
    funding_bps: Decimal
    hedge_basis_bps: Decimal
    execution_cost_bps: Decimal
    hedge_carry_bps: Decimal
    net_bps: Decimal


def carry_evidence_label(snapshot_id: str) -> str:
    return f"{_LABEL_PREFIX}:crypto_carry_v1:{snapshot_id}"


def _decimal(value: object, *, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid decimal {name}") from exc
    if not result.is_finite():
        raise ValueError(f"invalid decimal {name}")
    return result


def _datetime(value: object, *, name: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid {name} datetime") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return result


def _period(dataset: ResolvedDataset) -> tuple[date, date, datetime, datetime]:
    watermark = dataset.source_watermark
    start = _datetime(watermark.get("period_start"), name="period_start")
    end = _datetime(watermark.get("period_end"), name="period_end")
    if end <= start:
        raise ValueError("dataset period_end must be after period_start")
    return start.date(), end.date(), start, end


def _funding_facts(dataset: ResolvedDataset) -> tuple[_FundingFact, ...]:
    rows: list[_FundingFact] = []
    for row in dataset.rows:
        values = row.values
        if str(values.get("stream") or "") != "funding":
            continue
        observed = _datetime(
            values.get("observed_at", row.tradable_at),
            name="funding.observed_at",
        )
        rows.append(
            _FundingFact(
                rate=_decimal(values.get("funding_rate"), name="funding_rate"),
                observed_at=observed,
                tradable_at=row.tradable_at,
            )
        )
    rows.sort(key=lambda item: (item.tradable_at, item.observed_at))
    return tuple(rows)


def _price_facts(dataset: ResolvedDataset, stream: str) -> tuple[_PriceFact, ...]:
    rows: list[_PriceFact] = []
    for row in dataset.rows:
        values = row.values
        if str(values.get("stream") or "") != stream:
            continue
        observed = _datetime(
            values.get("observed_at", row.tradable_at),
            name=f"{stream}.observed_at",
        )
        price = _decimal(values.get("close"), name=f"{stream}.close")
        if price <= 0:
            continue
        rows.append(_PriceFact(price=price, observed_at=observed, tradable_at=row.tradable_at))
    rows.sort(key=lambda item: (item.tradable_at, item.observed_at))
    return tuple(rows)


def _latest_price(
    rows: Sequence[_PriceFact],
    times: Sequence[datetime],
    at: datetime,
) -> Decimal | None:
    end = bisect_right(times, at)
    return None if end <= 0 else rows[end - 1].price


def _interval_minutes(history: Sequence[_FundingFact]) -> int | None:
    visible = history[-_HISTORY_LIMIT:]
    if len(visible) < 2:
        return None
    deltas = [
        int((right.tradable_at - left.tradable_at).total_seconds() // 60)
        for left, right in zip(visible, visible[1:])
        if right.tradable_at > left.tradable_at
    ]
    if not deltas:
        return None
    result = int(statistics.median(deltas))
    return result if result > 0 else None


def _samples(
    funding: Sequence[_FundingFact],
    *,
    symbol: str,
    period_start: datetime,
    period_end: datetime,
) -> tuple[TimedSample, ...]:
    out: list[TimedSample] = []
    for index, current in enumerate(funding):
        maturity_index = index + TARGET_FUNDING_INTERVALS
        if index + 1 < MIN_FUNDING_OBSERVATIONS or maturity_index >= len(funding):
            continue
        maturity = funding[maturity_index].tradable_at
        if not (period_start <= current.tradable_at < period_end):
            continue
        if maturity > period_end:
            continue
        out.append(
            TimedSample(
                sample_id=f"funding|{current.observed_at.isoformat()}",
                observed_at=current.tradable_at,
                label_end_at=maturity,
                market_segment=symbol,
            )
        )
    out.sort(key=lambda item: item.observed_at)
    return tuple(out)


def _default_costs(cfg: EngineConfig) -> tuple[Decimal, Decimal, Decimal]:
    execution = _decimal(
        cfg.get("shadow.crypto_carry.execution_cost_bps"),
        name="execution_cost_bps",
    )
    hedge = _decimal(
        cfg.get("shadow.crypto_carry.hedge_carry_bps_per_interval"),
        name="hedge_carry_bps_per_interval",
    )
    uncertainty = _decimal(
        cfg.get("shadow.crypto_carry.funding_uncertainty_bps_per_interval"),
        name="funding_uncertainty_bps_per_interval",
    )
    if min(execution, hedge, uncertainty) < 0:
        raise ValueError("carry costs must be non-negative")
    return execution, hedge, uncertainty


def _candidate_trade(
    *,
    funding: Sequence[_FundingFact],
    funding_index: int,
    mark: Sequence[_PriceFact],
    mark_times: Sequence[datetime],
    index: Sequence[_PriceFact],
    index_times: Sequence[datetime],
    execution_cost_bps: Decimal,
    hedge_cost_bps_per_interval: Decimal,
    uncertainty_bps_per_interval: Decimal,
    instrument_id: str,
) -> _CarryTrade | None:
    current = funding[funding_index]
    history = funding[max(0, funding_index + 1 - _HISTORY_LIMIT) : funding_index + 1]
    interval_minutes = _interval_minutes(history)
    if interval_minutes is None:
        return None
    entry_mark = _latest_price(mark, mark_times, current.tradable_at)
    entry_index = _latest_price(index, index_times, current.tradable_at)
    if entry_mark is None or entry_index is None:
        return None

    observations = tuple(
        FundingObservation(
            rate=item.rate,
            settled_at=item.observed_at,
            tradable_at=item.tradable_at,
            source="bybit_snapshot_funding",
        )
        for item in history
    )
    facts = CryptoCarryMarketFacts(
        instrument_id=instrument_id,
        mark_price=entry_mark,
        index_price=entry_index,
        current_funding_rate=current.rate,
        funding_interval_minutes=interval_minutes,
        funding_history=observations,
        observed_at=current.observed_at,
        tradable_at=current.tradable_at,
        source="bybit_snapshot_settled_proxy_v1",
    )
    candidate = evaluate_crypto_carry_v1(
        facts=facts,
        execution_cost_bps=execution_cost_bps,
        hedge_carry_bps_per_interval=hedge_cost_bps_per_interval,
        funding_uncertainty_bps_per_interval=uncertainty_bps_per_interval,
        evaluated_at=current.tradable_at,
    )
    if candidate is None:
        return None

    future = funding[
        funding_index + 1 : funding_index + 1 + TARGET_FUNDING_INTERVALS
    ]
    if len(future) != TARGET_FUNDING_INTERVALS:
        return None
    maturity = future[-1].tradable_at
    exit_mark = _latest_price(mark, mark_times, maturity)
    exit_index = _latest_price(index, index_times, maturity)
    if exit_mark is None or exit_index is None:
        return None

    direction_sign = Decimal(1) if candidate.direction is Direction.LONG else Decimal(-1)
    funding_bps = sum(
        (-direction_sign * item.rate * Decimal("10000") for item in future),
        Decimal(0),
    )
    if candidate.direction is Direction.LONG:
        hedge_return = (
            (exit_mark - entry_mark) / entry_mark
            + (entry_index - exit_index) / entry_index
        )
    else:
        hedge_return = (
            (entry_mark - exit_mark) / entry_mark
            + (exit_index - entry_index) / entry_index
        )
    hedge_basis_bps = hedge_return * Decimal("10000")
    hedge_carry_bps = hedge_cost_bps_per_interval * Decimal(TARGET_FUNDING_INTERVALS)
    net_bps = funding_bps + hedge_basis_bps - execution_cost_bps - hedge_carry_bps
    return _CarryTrade(
        decision_at=current.tradable_at,
        maturity_at=maturity,
        direction=candidate.direction,
        funding_bps=funding_bps,
        hedge_basis_bps=hedge_basis_bps,
        execution_cost_bps=execution_cost_bps,
        hedge_carry_bps=hedge_carry_bps,
        net_bps=net_bps,
    )


def _profit_factor(rows: Sequence[_CarryTrade]) -> Decimal | None:
    gains = sum((row.net_bps for row in rows if row.net_bps > 0), Decimal(0))
    losses = abs(sum((row.net_bps for row in rows if row.net_bps < 0), Decimal(0)))
    return None if losses <= 0 else gains / losses


def _top5_contribution(rows: Sequence[_CarryTrade]) -> Decimal:
    positives = sorted((row.net_bps for row in rows if row.net_bps > 0), reverse=True)
    total = sum(positives, Decimal(0))
    if total <= 0:
        return Decimal(1)
    return sum(positives[:5], Decimal(0)) / total


def _max_drawdown_bps(rows: Sequence[_CarryTrade]) -> Decimal:
    equity = Decimal(0)
    peak = Decimal(0)
    worst = Decimal(0)
    for row in rows:
        equity += row.net_bps
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _trade_sharpe(rows: Sequence[_CarryTrade]) -> Decimal | None:
    if len(rows) < 2:
        return None
    values = [float(row.net_bps) for row in rows]
    deviation = statistics.stdev(values)
    if deviation <= 0:
        return None
    return Decimal(str(statistics.fmean(values) / deviation))


def _trade_sortino(rows: Sequence[_CarryTrade]) -> Decimal | None:
    if not rows:
        return None
    values = [float(row.net_bps) for row in rows]
    downside = [min(value, 0.0) for value in values]
    deviation = sqrt(sum(value * value for value in downside) / len(downside))
    if deviation <= 0:
        return None
    return Decimal(str(statistics.fmean(values) / deviation))


def _blocked_run(
    session: Session,
    *,
    dataset: ResolvedDataset,
    cfg: EngineConfig,
    reason: str,
    detail: dict[str, object] | None = None,
) -> BacktestRun:
    period_from, period_to, _start, _end = _period(dataset)
    symbol = str(dataset.source_watermark.get("symbol") or "")
    run = BacktestRun(
        label=carry_evidence_label(dataset.snapshot_id),
        strategy="crypto_carry_v1",
        period_from=period_from,
        period_to=period_to,
        config_hash=cfg.config_hash,
        engine_version=ENGINE_VERSION,
        universe_json=["CRYPTO", "BYBIT", symbol] if symbol else ["CRYPTO", "BYBIT"],
        trades=0,
        net_return=None,
        profit_factor=None,
        expectancy_r=None,
        max_drawdown=None,
        sharpe=None,
        sortino=None,
        calmar=None,
        brier_score=None,
        pbo=None,
        top5_contribution=None,
        report_json={
            "metric_space": _METRIC_SPACE,
            "outcome_metric": _OUTCOME_METRIC,
            "dataset": dataset.audit,
            "reason": reason,
            **dict(detail or {}),
        },
        gate_passed=False,
        gate_detail_json={
            "metric_space": _METRIC_SPACE,
            "dataset_readiness": dataset.source_watermark.get("readiness"),
            "reason": reason,
        },
    )
    session.add(run)
    return run


def run_bybit_carry_backtest(
    session: Session,
    *,
    dataset: ResolvedDataset,
    walk_forward: WalkForwardConfig,
    min_trades: int,
    min_profit_factor: Decimal,
    max_top5_contribution: Decimal,
    cfg: EngineConfig,
) -> BacktestRun:
    """Replay ``crypto_carry_v1`` on one exact immutable Bybit snapshot."""

    if str(dataset.source_watermark.get("readiness") or "") != DATA_READY:
        return _blocked_run(session, dataset=dataset, cfg=cfg, reason="DATA_BLOCKED")
    if min_trades < 1 or min_profit_factor < 0:
        raise ValueError("invalid carry gate")
    if max_top5_contribution < 0 or max_top5_contribution > 1:
        raise ValueError("invalid carry concentration gate")

    period_from, period_to, period_start, period_end = _period(dataset)
    symbol = str(dataset.source_watermark.get("symbol") or "")
    instrument_id = f"CRYPTO:{symbol}" if symbol else dataset.dataset_name
    funding = _funding_facts(dataset)
    mark = _price_facts(dataset, "mark_price")
    index = _price_facts(dataset, "index_price")
    if len(funding) < MIN_FUNDING_OBSERVATIONS + TARGET_FUNDING_INTERVALS:
        return _blocked_run(
            session,
            dataset=dataset,
            cfg=cfg,
            reason="FUNDING_HISTORY_INSUFFICIENT",
            detail={"funding_rows": len(funding)},
        )
    if not mark or not index:
        return _blocked_run(
            session,
            dataset=dataset,
            cfg=cfg,
            reason="MARK_INDEX_HISTORY_UNAVAILABLE",
            detail={"mark_rows": len(mark), "index_rows": len(index)},
        )

    samples = _samples(
        funding,
        symbol=symbol or dataset.dataset_name,
        period_start=period_start,
        period_end=period_end,
    )
    folds = purged_walk_forward(samples, walk_forward)
    if not folds:
        return _blocked_run(
            session,
            dataset=dataset,
            cfg=cfg,
            reason="WALK_FORWARD_EMPTY",
            detail={"samples": len(samples)},
        )

    execution_cost, hedge_cost, uncertainty = _default_costs(cfg)
    funding_index = {item.tradable_at: pos for pos, item in enumerate(funding)}
    mark_times = tuple(item.tradable_at for item in mark)
    index_times = tuple(item.tradable_at for item in index)
    trades: list[_CarryTrade] = []
    fold_evidence: list[dict[str, object]] = []
    next_allowed_at: datetime | None = None
    seen_decisions: set[datetime] = set()

    for fold in folds:
        signals = 0
        labeled = 0
        overlap_skipped = 0
        for sample in fold.test:
            at = sample.observed_at
            if at in seen_decisions:
                continue
            if next_allowed_at is not None and at < next_allowed_at:
                overlap_skipped += 1
                continue
            pos = funding_index.get(at)
            if pos is None:
                raise ValueError("carry sample does not map to immutable funding fact")
            trade = _candidate_trade(
                funding=funding,
                funding_index=pos,
                mark=mark,
                mark_times=mark_times,
                index=index,
                index_times=index_times,
                execution_cost_bps=execution_cost,
                hedge_cost_bps_per_interval=hedge_cost,
                uncertainty_bps_per_interval=uncertainty,
                instrument_id=instrument_id,
            )
            seen_decisions.add(at)
            if trade is None:
                continue
            signals += 1
            trades.append(trade)
            labeled += 1
            next_allowed_at = trade.maturity_at
        fold_evidence.append(
            {
                "fold_index": fold.fold_index,
                "train_start": fold.train_start.isoformat(),
                "train_end": fold.train_end.isoformat(),
                "validation_start": fold.validation_start.isoformat(),
                "validation_end": fold.validation_end.isoformat(),
                "test_start": fold.test_start.isoformat(),
                "test_end": fold.test_end.isoformat(),
                "train_samples": len(fold.train),
                "validation_samples": len(fold.validation),
                "test_samples": len(fold.test),
                "signals": signals,
                "labeled_trades": labeled,
                "overlap_skipped": overlap_skipped,
                "purged": len(fold.purged_sample_ids),
                "embargoed": len(fold.embargoed_sample_ids),
            }
        )

    trades.sort(key=lambda item: item.decision_at)
    if not trades:
        return _blocked_run(
            session,
            dataset=dataset,
            cfg=cfg,
            reason="NO_OOS_TRADES",
            detail={"folds": fold_evidence},
        )

    expectancy = sum((item.net_bps for item in trades), Decimal(0)) / Decimal(len(trades))
    profit_factor = _profit_factor(trades)
    max_drawdown = _max_drawdown_bps(trades)
    top5 = _top5_contribution(trades)
    sharpe = _trade_sharpe(trades)
    sortino = _trade_sortino(trades)
    gains = [item.net_bps for item in trades if item.net_bps > 0]
    losses = [item.net_bps for item in trades if item.net_bps < 0]
    profit_factor_infinite = bool(gains) and not losses
    min_expectancy_bps = Decimal(str(MIN_NET_CARRY_BPS))
    criteria = {
        "min_trades": len(trades) >= min_trades,
        "min_profit_factor": (
            profit_factor_infinite
            or (profit_factor is not None and profit_factor >= min_profit_factor)
        ),
        "min_expectancy_bps": expectancy >= min_expectancy_bps,
        "max_top5_contribution": top5 <= max_top5_contribution,
    }
    gate_passed = all(criteria.values())
    total_funding = sum((item.funding_bps for item in trades), Decimal(0))
    total_hedge_basis = sum((item.hedge_basis_bps for item in trades), Decimal(0))
    total_cost = sum(
        (item.execution_cost_bps + item.hedge_carry_bps for item in trades),
        Decimal(0),
    )
    total_net = sum((item.net_bps for item in trades), Decimal(0))
    report = {
        "metric_space": _METRIC_SPACE,
        "outcome_metric": _OUTCOME_METRIC,
        "account_return_not_modeled": True,
        "directional_r_not_applicable": True,
        "dataset": dataset.audit,
        "cost_model": {
            "execution_cost_bps": str(execution_cost),
            "execution_cost_source": "approved_crypto_perpetual_full_entry_exit_friction",
            "hedge_carry_bps_per_interval": str(hedge_cost),
            "hedge_carry_source": "approved_shadow_crypto_carry_assumption",
            "funding_uncertainty_bps_per_interval": str(uncertainty),
            "funding_uncertainty_applied_to_candidate_selection": True,
            "funding_uncertainty_applied_to_realized_outcome": False,
        },
        "methodology": {
            "decision_clock": "settled_funding_tradable_at",
            "current_funding_proxy": "latest_settled_funding_known_point_in_time",
            "funding_history_limit": _HISTORY_LIMIT,
            "holding_funding_intervals": TARGET_FUNDING_INTERVALS,
            "overlapping_positions": "suppressed_until_prior_maturity",
            "hedge_label": "perpetual_mark_leg_plus_index_reference_hedge_leg",
        },
        "walk_forward": {"folds": fold_evidence},
        "oos": {
            "folds": len(folds),
            "trades": len(trades),
            "net_total_bps": str(total_net),
            "funding_total_bps": str(total_funding),
            "hedge_basis_total_bps": str(total_hedge_basis),
            "modeled_cost_total_bps": str(total_cost),
            "expectancy_bps": str(expectancy),
            "profit_factor": (
                "INF"
                if profit_factor_infinite
                else (None if profit_factor is None else str(profit_factor))
            ),
            "profit_factor_is_infinite": profit_factor_infinite,
            "max_drawdown_bps": str(max_drawdown),
            "trade_sharpe_nonannualized": None if sharpe is None else str(sharpe),
            "trade_sortino_nonannualized": None if sortino is None else str(sortino),
            "win_rate": str(Decimal(len(gains)) / Decimal(len(trades))),
            "losses": len(losses),
            "top5_contribution": str(top5),
            "pbo": None,
            "pbo_reason": "single_strategy_run_requires_cross_variant_matrix",
        },
    }
    run = BacktestRun(
        label=carry_evidence_label(dataset.snapshot_id),
        strategy="crypto_carry_v1",
        period_from=period_from,
        period_to=period_to,
        config_hash=cfg.config_hash,
        engine_version=ENGINE_VERSION,
        universe_json=["CRYPTO", "BYBIT", symbol] if symbol else ["CRYPTO", "BYBIT"],
        trades=len(trades),
        net_return=None,
        profit_factor=profit_factor,
        expectancy_r=None,
        max_drawdown=None,
        sharpe=sharpe,
        sortino=sortino,
        calmar=None,
        brier_score=None,
        pbo=None,
        top5_contribution=top5,
        report_json=report,
        gate_passed=gate_passed,
        gate_detail_json={
            "metric_space": _METRIC_SPACE,
            "dataset_readiness": DATA_READY,
            "criteria": criteria,
            "thresholds": {
                "min_trades": min_trades,
                "min_profit_factor": str(min_profit_factor),
                "min_expectancy_bps": str(min_expectancy_bps),
                "max_top5_contribution": str(max_top5_contribution),
            },
            "r_threshold_not_applicable": True,
        },
    )
    session.add(run)
    return run


__all__ = ["carry_evidence_label", "run_bybit_carry_backtest"]
