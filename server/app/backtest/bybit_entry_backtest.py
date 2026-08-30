"""Scheduled immutable Bybit entry-strategy backtests in R-multiple space.

This module deliberately does **not** reuse percentage-return robustness
objects. The approved Paper A/B outcome contract is directional alpha measured
in R: horizon price move divided by a PIT Wilder-ATR risk unit, less explicit
round-trip costs. Treating those R values as account returns would manufacture
portfolio economics that do not exist at the entry-strategy layer.

Only strategies whose historical inputs are actually present in the immutable
snapshot are evaluated. Missing historical spread or a missing settled-funding
outcome produces an explicit blocked BacktestRun rather than a surrogate fact.
"""

from __future__ import annotations

import statistics
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from math import sqrt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from ..datasets.snapshots import (
    DatasetSnapshotResolver,
    FilesystemSnapshotStore,
    ResolvedDataset,
)
from ..features.indicators import atr
from ..market.candles import Candle, resample_hours
from ..models import BacktestRun
from ..models.enums import Direction
from ..strategies.momentum_v2 import evaluate_momentum_v2
from ..version import ENGINE_VERSION
from .bybit_dataset import DATA_READY
from .walk_forward import TimedSample, WalkForwardConfig, purged_walk_forward

_OUTCOME_METRIC = "paper_directional_alpha_r_v1"
_DEFAULT_LABEL_HORIZON = timedelta(hours=24)
_R4_STRATEGIES = (
    "momentum_v2",
    "mean_reversion_v1",
    "breakout_v2",
    "crypto_carry_v1",
)
_BLOCKERS = {
    # Both strategies need a point-in-time liquidity/spread state. Historical
    # OHLCV/mark/index/premium cannot reconstruct the bid/ask spread honestly.
    "mean_reversion_v1": "HISTORICAL_SPREAD_UNAVAILABLE",
    "breakout_v2": "HISTORICAL_SPREAD_UNAVAILABLE",
    # Carry needs the realised settled-funding + hedge outcome, not a price-only
    # label. The live Paper A/B path intentionally fails closed on the same gap.
    "crypto_carry_v1": "CARRY_SETTLED_FUNDING_OUTCOME_UNAVAILABLE",
}


@dataclass(frozen=True, slots=True)
class EntrySignal:
    direction: Direction
    entry_reference: Decimal
    horizon: timedelta
    regime: str

    def __post_init__(self) -> None:
        if not isinstance(self.direction, Direction):
            raise ValueError("direction must be Direction")
        if (
            not isinstance(self.entry_reference, Decimal)
            or not self.entry_reference.is_finite()
            or self.entry_reference <= 0
        ):
            raise ValueError("entry_reference must be a positive finite Decimal")
        if self.horizon <= timedelta(0):
            raise ValueError("horizon must be positive")
        if not self.regime.strip():
            raise ValueError("regime must not be blank")


@dataclass(frozen=True, slots=True)
class EntryReplayGate:
    min_trades: int = 200
    min_profit_factor: Decimal = Decimal("1.20")
    min_expectancy_r: Decimal = Decimal("0.12")
    max_top5_contribution: Decimal = Decimal("0.30")

    def __post_init__(self) -> None:
        if self.min_trades < 1:
            raise ValueError("min_trades must be positive")
        if self.min_profit_factor < 0:
            raise ValueError("min_profit_factor must not be negative")
        if self.max_top5_contribution < 0 or self.max_top5_contribution > 1:
            raise ValueError("max_top5_contribution must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class _BarFact:
    candle: Candle
    tradable_at: datetime


@dataclass(frozen=True, slots=True)
class _RTrade:
    decision_at: datetime
    gross_r: Decimal
    net_r: Decimal
    mae_r: Decimal
    mfe_r: Decimal
    regime: str


SignalEvaluator = Callable[..., EntrySignal | None]


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


def _bar_facts(dataset: ResolvedDataset) -> tuple[_BarFact, ...]:
    rows: list[_BarFact] = []
    for row in dataset.rows:
        values = row.values
        if str(values.get("stream") or "") != "klines":
            continue
        observed = _datetime(
            values.get("observed_at", row.tradable_at),
            name="klines.observed_at",
        )
        close = _decimal(values.get("close"), name="close")
        open_price = _decimal(values.get("open", close), name="open")
        high = _decimal(values.get("high", max(open_price, close)), name="high")
        low = _decimal(values.get("low", min(open_price, close)), name="low")
        rows.append(
            _BarFact(
                candle=Candle(
                    open_time=observed,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume_units=(
                        None
                        if values.get("volume_units") is None
                        else _decimal(values.get("volume_units"), name="volume_units")
                    ),
                    volume_notional=(
                        None
                        if values.get("volume_notional") is None
                        else _decimal(
                            values.get("volume_notional"), name="volume_notional"
                        )
                    ),
                    open_interest=(
                        None
                        if values.get("open_interest") is None
                        else _decimal(values.get("open_interest"), name="open_interest")
                    ),
                    is_closed=True,
                    source="bybit_snapshot",
                ),
                tradable_at=row.tradable_at,
            )
        )
    rows.sort(key=lambda item: (item.tradable_at, item.candle.open_time))
    return tuple(rows)


def _samples(
    bars: Sequence[_BarFact],
    *,
    symbol: str,
    period_start: datetime,
    period_end: datetime,
    label_horizon: timedelta,
) -> tuple[TimedSample, ...]:
    samples = [
        TimedSample(
            sample_id=f"klines|{item.candle.open_time.isoformat()}",
            observed_at=item.tradable_at,
            label_end_at=item.tradable_at + label_horizon,
            market_segment=symbol,
        )
        for item in bars
        if period_start <= item.candle.open_time < period_end
        and item.tradable_at <= period_end
    ]
    samples.sort(key=lambda item: item.observed_at)
    return tuple(samples)


def _default_walk_forward(cfg: EngineConfig) -> WalkForwardConfig:
    raw = cfg.get("backtest.walk_forward")
    day = 30
    return WalkForwardConfig(
        train_span=timedelta(days=int(raw["train_months"]) * day),
        validation_span=timedelta(days=int(raw["validation_months"]) * day),
        test_span=timedelta(days=int(raw["test_months"]) * day),
        embargo=timedelta(days=1),
        step=timedelta(days=int(raw["step_months"]) * day),
    )


def _default_gate(cfg: EngineConfig) -> EntryReplayGate:
    raw = cfg.get("backtest.paper_gate")
    return EntryReplayGate(
        min_trades=int(raw["min_aggregate_trades"]),
        min_profit_factor=Decimal(str(raw["min_oos_profit_factor"])),
        min_expectancy_r=Decimal(str(raw["min_oos_expectancy_r"])),
        max_top5_contribution=Decimal(str(raw["max_top5_contribution"])),
    )


def _default_round_trip_cost_bps(cfg: EngineConfig) -> Decimal:
    value = Decimal(str(cfg.get("shadow.crypto_carry.execution_cost_bps")))
    if not value.is_finite() or value < 0:
        raise ValueError("configured Bybit round-trip cost must be non-negative")
    return value


def _momentum_signal(
    *,
    instrument_id: str,
    context_bars: Sequence[Candle],
    setup_bars: Sequence[Candle],
    trigger_bars: Sequence[Candle],
    evaluated_at: datetime,
    **_kwargs,
) -> EntrySignal | None:
    candidate = evaluate_momentum_v2(
        instrument_id=instrument_id,
        context_bars=context_bars,
        setup_bars=setup_bars,
        trigger_bars=trigger_bars,
        evaluated_at=evaluated_at,
    )
    if candidate is None:
        return None
    unit = candidate.horizon.unit.upper()
    if unit == "HOURS":
        horizon = timedelta(hours=candidate.horizon.value)
    elif unit == "MINUTES":
        horizon = timedelta(minutes=candidate.horizon.value)
    elif unit == "DAYS":
        horizon = timedelta(days=candidate.horizon.value)
    else:
        raise ValueError(f"unsupported candidate horizon unit: {candidate.horizon.unit}")
    return EntrySignal(
        direction=candidate.direction,
        entry_reference=candidate.entry_hypothesis.reference,
        horizon=horizon,
        regime=candidate.strategy_family,
    )


def _atr_risk_unit(trigger_bars: Sequence[Candle]) -> Decimal | None:
    values = atr(trigger_bars, 14)
    return next(
        (value for value in reversed(values) if value is not None and value > 0),
        None,
    )


def _label_trade(
    *,
    signal: EntrySignal,
    decision_at: datetime,
    risk_unit: Decimal,
    future: Sequence[_BarFact],
    round_trip_cost_bps: Decimal,
) -> _RTrade | None:
    maturity = decision_at + signal.horizon
    exit_index = next(
        (index for index, item in enumerate(future) if item.tradable_at >= maturity),
        None,
    )
    if exit_index is None:
        return None
    path = future[: exit_index + 1]
    exit_price = path[-1].candle.close
    entry = signal.entry_reference
    if signal.direction is Direction.LONG:
        gross_r = (exit_price - entry) / risk_unit
        excursions = [
            ((item.candle.low - entry) / risk_unit, (item.candle.high - entry) / risk_unit)
            for item in path
        ]
    else:
        gross_r = (entry - exit_price) / risk_unit
        excursions = [
            ((entry - item.candle.high) / risk_unit, (entry - item.candle.low) / risk_unit)
            for item in path
        ]
    cost_r = entry * round_trip_cost_bps / Decimal("10000") / risk_unit
    return _RTrade(
        decision_at=decision_at,
        gross_r=gross_r,
        net_r=gross_r - cost_r,
        mae_r=min(Decimal(0), min((item[0] for item in excursions), default=Decimal(0))),
        mfe_r=max(Decimal(0), max((item[1] for item in excursions), default=Decimal(0))),
        regime=signal.regime,
    )


def _top5_contribution(rows: Sequence[_RTrade]) -> Decimal:
    positives = sorted((row.net_r for row in rows if row.net_r > 0), reverse=True)
    total = sum(positives, Decimal(0))
    if total <= 0:
        return Decimal(1)
    return sum(positives[:5], Decimal(0)) / total


def _max_drawdown_r(rows: Sequence[_RTrade]) -> Decimal:
    equity = Decimal(0)
    peak = Decimal(0)
    worst = Decimal(0)
    for row in rows:
        equity += row.net_r
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _trade_sharpe(rows: Sequence[_RTrade]) -> Decimal | None:
    if len(rows) < 2:
        return None
    values = [float(row.net_r) for row in rows]
    deviation = statistics.stdev(values)
    if deviation <= 0:
        return None
    return Decimal(str(statistics.fmean(values) / deviation))


def _trade_sortino(rows: Sequence[_RTrade]) -> Decimal | None:
    if not rows:
        return None
    values = [float(row.net_r) for row in rows]
    downside = [min(value, 0.0) for value in values]
    deviation = sqrt(sum(value * value for value in downside) / len(downside))
    if deviation <= 0:
        return None
    return Decimal(str(statistics.fmean(values) / deviation))


def _profit_factor(rows: Sequence[_RTrade]) -> Decimal | None:
    gains = sum((row.net_r for row in rows if row.net_r > 0), Decimal(0))
    losses = abs(sum((row.net_r for row in rows if row.net_r < 0), Decimal(0)))
    return gains / losses if losses > 0 else (None if gains <= 0 else Decimal("999999"))


def _label(strategy_version: str, snapshot_id: str) -> str:
    return f"bybit-entry-backtest:{strategy_version}:{snapshot_id}"


def _blocked_run(
    session: Session,
    *,
    dataset: ResolvedDataset,
    strategy_version: str,
    cfg: EngineConfig,
    reason: str,
    detail: Mapping[str, object] | None = None,
) -> BacktestRun:
    period_from, period_to, _start, _end = _period(dataset)
    symbol = str(dataset.source_watermark.get("symbol") or "")
    run = BacktestRun(
        label=_label(strategy_version, dataset.snapshot_id),
        strategy=strategy_version,
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
            "metric_space": "R_MULTIPLES",
            "outcome_metric": _OUTCOME_METRIC,
            "dataset": dataset.audit,
            "reason": reason,
            **dict(detail or {}),
        },
        gate_passed=False,
        gate_detail_json={
            "dataset_readiness": dataset.source_watermark.get("readiness"),
            "reason": reason,
        },
    )
    session.add(run)
    return run


def run_bybit_entry_backtest(
    session: Session,
    *,
    store: FilesystemSnapshotStore,
    snapshot_id: str,
    strategy_version: str,
    signal_evaluator: SignalEvaluator | None = None,
    walk_forward: WalkForwardConfig | None = None,
    gate: EntryReplayGate | None = None,
    round_trip_cost_bps: Decimal | None = None,
    cfg: EngineConfig | None = None,
) -> BacktestRun:
    """Evaluate one R4 entry strategy from an exact immutable snapshot only."""

    if strategy_version not in _R4_STRATEGIES:
        raise ValueError(f"unsupported Bybit R4 strategy: {strategy_version}")
    config = cfg or get_config()
    dataset = DatasetSnapshotResolver(session, store=store).resolve_snapshot_id(snapshot_id)
    if not dataset.dataset_name.startswith("bybit:"):
        raise ValueError("Bybit entry backtest requires a bybit:* dataset")
    if str(dataset.source_watermark.get("readiness") or "") != DATA_READY:
        return _blocked_run(
            session,
            dataset=dataset,
            strategy_version=strategy_version,
            cfg=config,
            reason="DATA_BLOCKED",
        )
    blocker = _BLOCKERS.get(strategy_version)
    if blocker is not None:
        return _blocked_run(
            session,
            dataset=dataset,
            strategy_version=strategy_version,
            cfg=config,
            reason=blocker,
        )

    evaluator = signal_evaluator or _momentum_signal
    threshold = gate or _default_gate(config)
    wf = walk_forward or _default_walk_forward(config)
    costs = (
        _default_round_trip_cost_bps(config)
        if round_trip_cost_bps is None
        else round_trip_cost_bps
    )
    if not isinstance(costs, Decimal) or not costs.is_finite() or costs < 0:
        raise ValueError("round_trip_cost_bps must be a non-negative Decimal")

    period_from, period_to, period_start, period_end = _period(dataset)
    symbol = str(dataset.source_watermark.get("symbol") or "")
    instrument_id = f"CRYPTO:PERP:{symbol}" if symbol else dataset.dataset_name
    bars = _bar_facts(dataset)
    if not bars:
        return _blocked_run(
            session,
            dataset=dataset,
            strategy_version=strategy_version,
            cfg=config,
            reason="KLINES_UNAVAILABLE",
        )

    samples = _samples(
        bars,
        symbol=symbol or dataset.dataset_name,
        period_start=period_start,
        period_end=period_end,
        label_horizon=_DEFAULT_LABEL_HORIZON,
    )
    folds = purged_walk_forward(samples, wf)
    if not folds:
        return _blocked_run(
            session,
            dataset=dataset,
            strategy_version=strategy_version,
            cfg=config,
            reason="WALK_FORWARD_EMPTY",
            detail={"samples": len(samples)},
        )

    h1 = tuple(item.candle for item in bars)
    h1_available = tuple(item.tradable_at for item in bars)
    h4 = tuple(resample_hours(h1, 4, session_start_hour_utc=0))
    d1 = tuple(resample_hours(h1, 24, session_start_hour_utc=0))
    h4_available = tuple(item.open_time + timedelta(hours=4) for item in h4)
    d1_available = tuple(item.open_time + timedelta(hours=24) for item in d1)

    trades: list[_RTrade] = []
    fold_evidence: list[dict[str, object]] = []
    seen_decisions: set[datetime] = set()
    for fold in folds:
        fold_signals = 0
        fold_labeled = 0
        allowed = {sample.observed_at for sample in fold.test}
        for sample in fold.test:
            at = sample.observed_at
            h1_end = bisect_right(h1_available, at)
            h4_end = bisect_right(h4_available, at)
            d1_end = bisect_right(d1_available, at)
            trigger = h1[max(0, h1_end - 800) : h1_end]
            setup = h4[max(0, h4_end - 400) : h4_end]
            context = d1[max(0, d1_end - 400) : d1_end]
            signal = evaluator(
                instrument_id=instrument_id,
                context_bars=context,
                setup_bars=setup,
                trigger_bars=trigger,
                evaluated_at=at,
                dataset=dataset,
                config=config,
            )
            if signal is None:
                continue
            if not isinstance(signal, EntrySignal):
                raise ValueError("signal_evaluator must return EntrySignal or None")
            if at not in allowed or at in seen_decisions:
                raise ValueError("entry evaluator emitted outside unique OOS test timestamps")
            fold_signals += 1
            risk_unit = _atr_risk_unit(trigger)
            if risk_unit is None:
                continue
            future = bars[h1_end:]
            trade = _label_trade(
                signal=signal,
                decision_at=at,
                risk_unit=risk_unit,
                future=future,
                round_trip_cost_bps=costs,
            )
            if trade is None:
                continue
            seen_decisions.add(at)
            trades.append(trade)
            fold_labeled += 1
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
                "signals": fold_signals,
                "labeled_trades": fold_labeled,
                "purged": len(fold.purged_sample_ids),
                "embargoed": len(fold.embargoed_sample_ids),
            }
        )

    trades.sort(key=lambda item: item.decision_at)
    if not trades:
        return _blocked_run(
            session,
            dataset=dataset,
            strategy_version=strategy_version,
            cfg=config,
            reason="NO_OOS_TRADES",
            detail={"folds": fold_evidence},
        )

    expectancy = sum((item.net_r for item in trades), Decimal(0)) / Decimal(len(trades))
    profit_factor = _profit_factor(trades)
    max_drawdown_r = _max_drawdown_r(trades)
    top5 = _top5_contribution(trades)
    sharpe = _trade_sharpe(trades)
    sortino = _trade_sortino(trades)
    criteria = {
        "min_trades": len(trades) >= threshold.min_trades,
        "min_profit_factor": (
            profit_factor is not None and profit_factor >= threshold.min_profit_factor
        ),
        "min_expectancy_r": expectancy >= threshold.min_expectancy_r,
        "max_top5_contribution": top5 <= threshold.max_top5_contribution,
    }
    gate_passed = all(criteria.values())
    gains = [item.net_r for item in trades if item.net_r > 0]
    losses = [item.net_r for item in trades if item.net_r < 0]
    report = {
        "metric_space": "R_MULTIPLES",
        "outcome_metric": _OUTCOME_METRIC,
        "account_return_not_modeled": True,
        "dataset": dataset.audit,
        "cost_model": {
            "round_trip_cost_bps": str(costs),
            "source": "approved_crypto_perpetual_full_entry_exit_friction",
        },
        "walk_forward": {
            "month_basis_days": 30 if walk_forward is None else None,
            "folds": fold_evidence,
        },
        "oos": {
            "folds": len(folds),
            "trades": len(trades),
            "net_total_r": str(sum((item.net_r for item in trades), Decimal(0))),
            "gross_total_r": str(sum((item.gross_r for item in trades), Decimal(0))),
            "expectancy_r": str(expectancy),
            "profit_factor": None if profit_factor is None else str(profit_factor),
            "max_drawdown_r": str(max_drawdown_r),
            "trade_sharpe_nonannualized": None if sharpe is None else str(sharpe),
            "trade_sortino_nonannualized": None if sortino is None else str(sortino),
            "win_rate": str(Decimal(len(gains)) / Decimal(len(trades))),
            "losses": len(losses),
            "top5_contribution": str(top5),
            "average_mae_r": str(
                sum((item.mae_r for item in trades), Decimal(0)) / Decimal(len(trades))
            ),
            "average_mfe_r": str(
                sum((item.mfe_r for item in trades), Decimal(0)) / Decimal(len(trades))
            ),
            "pbo": None,
            "pbo_reason": "single_strategy_run_requires_cross_variant_matrix",
        },
    }
    run = BacktestRun(
        label=_label(strategy_version, dataset.snapshot_id),
        strategy=strategy_version,
        period_from=period_from,
        period_to=period_to,
        config_hash=config.config_hash,
        engine_version=ENGINE_VERSION,
        universe_json=["CRYPTO", "BYBIT", symbol] if symbol else ["CRYPTO", "BYBIT"],
        trades=len(trades),
        # Entry-strategy evidence is in R, not account-return space.
        net_return=None,
        profit_factor=profit_factor,
        expectancy_r=expectancy,
        max_drawdown=max_drawdown_r,
        sharpe=sharpe,
        sortino=sortino,
        calmar=None,
        brier_score=None,
        pbo=None,
        top5_contribution=top5,
        report_json=report,
        gate_passed=gate_passed,
        gate_detail_json={
            "dataset_readiness": DATA_READY,
            "criteria": criteria,
            "thresholds": {
                "min_trades": threshold.min_trades,
                "min_profit_factor": str(threshold.min_profit_factor),
                "min_expectancy_r": str(threshold.min_expectancy_r),
                "max_top5_contribution": str(threshold.max_top5_contribution),
            },
        },
    )
    session.add(run)
    return run


def run_pending_bybit_entry_backtests(
    session: Session,
    *,
    store: FilesystemSnapshotStore,
    snapshot_id: str,
    signal_evaluators: Mapping[str, SignalEvaluator] | None = None,
    walk_forward: WalkForwardConfig | None = None,
    gate: EntryReplayGate | None = None,
    round_trip_cost_bps: Decimal | None = None,
    cfg: EngineConfig | None = None,
) -> tuple[BacktestRun, ...]:
    """Create at most one immutable evidence run per R4 strategy/snapshot."""

    config = cfg or get_config()
    evaluators = dict(signal_evaluators or {})
    created: list[BacktestRun] = []
    for strategy_version in _R4_STRATEGIES:
        label = _label(strategy_version, snapshot_id)
        existing = session.execute(
            select(BacktestRun.id).where(BacktestRun.label == label).limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            continue
        created.append(
            run_bybit_entry_backtest(
                session,
                store=store,
                snapshot_id=snapshot_id,
                strategy_version=strategy_version,
                signal_evaluator=evaluators.get(strategy_version),
                walk_forward=walk_forward,
                gate=gate,
                round_trip_cost_bps=round_trip_cost_bps,
                cfg=config,
            )
        )
        session.flush()
    return tuple(created)


__all__ = [
    "EntryReplayGate",
    "EntrySignal",
    "SignalEvaluator",
    "run_bybit_entry_backtest",
    "run_pending_bybit_entry_backtests",
]
