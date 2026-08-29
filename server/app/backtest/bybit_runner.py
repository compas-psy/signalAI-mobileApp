"""Replay-safe Bybit walk-forward runner persisted as BacktestRun evidence.

The runner has no market HTTP dependency. Its only market input is one exact,
checksum-verified DatasetSnapshot identity. Strategy replay is injected as a
pure evaluator receiving the immutable resolved dataset and a purged
walk-forward fold; therefore the orchestration layer cannot silently switch to
current Bybit facts during historical evaluation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Callable, Iterable

from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from ..datasets.snapshots import (
    DatasetRow,
    DatasetSnapshotResolver,
    FilesystemSnapshotStore,
    ResolvedDataset,
)
from ..models import BacktestRun
from ..version import ENGINE_VERSION
from .bybit_dataset import DATA_READY
from .robustness import PathObservation, compute_robustness_report
from .walk_forward import TimedSample, WalkForwardConfig, WalkForwardFold, purged_walk_forward


@dataclass(frozen=True, slots=True)
class ReplayGate:
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


ReplayEvaluator = Callable[
    [ResolvedDataset, WalkForwardFold],
    Iterable[PathObservation],
]


def _parse_datetime(value: object, *, name: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise ValueError(f"invalid {name} datetime") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return result


def _dataset_period(dataset: ResolvedDataset) -> tuple[date, date]:
    watermark = dataset.source_watermark
    raw_start = watermark.get("period_start")
    raw_end = watermark.get("period_end")
    if raw_start is not None and raw_end is not None:
        return (
            _parse_datetime(raw_start, name="period_start").date(),
            _parse_datetime(raw_end, name="period_end").date(),
        )
    times = [row.tradable_at.date() for row in dataset.rows]
    if not times:
        day = dataset.tradable_at.date()
        return day, day
    return min(times), max(times)


def _samples(
    dataset: ResolvedDataset,
    *,
    label_horizon: timedelta,
) -> tuple[TimedSample, ...]:
    if label_horizon <= timedelta(0):
        raise ValueError("label_horizon must be positive")
    symbol = str(dataset.source_watermark.get("symbol") or dataset.dataset_name)
    samples: list[TimedSample] = []
    for row in dataset.rows:
        if str(row.values.get("stream") or "") != "klines":
            continue
        raw_observed = row.values.get("observed_at")
        observed = (
            row.tradable_at
            if raw_observed is None
            else _parse_datetime(raw_observed, name="observed_at")
        )
        # The signal sample cannot exist before the bar/feature is tradable.
        available = max(observed, row.tradable_at)
        samples.append(
            TimedSample(
                sample_id=row.key,
                observed_at=available,
                label_end_at=available + label_horizon,
                market_segment=symbol,
            )
        )
    samples.sort(key=lambda item: item.observed_at)
    return tuple(samples)


def _top5_contribution(rows: tuple[PathObservation, ...]) -> Decimal:
    positives = sorted(
        (row.net_return for row in rows if row.net_return > 0), reverse=True
    )
    total = sum(positives, Decimal(0))
    if total <= 0:
        return Decimal(1)
    return sum(positives[:5], Decimal(0)) / total


def _default_walk_forward(cfg: EngineConfig) -> WalkForwardConfig:
    raw = cfg.get("backtest.walk_forward")
    # Existing splitter is timedelta-based. Keep the month basis explicit in
    # persisted evidence instead of pretending this is calendar-month math.
    day = 30
    return WalkForwardConfig(
        train_span=timedelta(days=int(raw["train_months"]) * day),
        validation_span=timedelta(days=int(raw["validation_months"]) * day),
        test_span=timedelta(days=int(raw["test_months"]) * day),
        embargo=timedelta(days=1),
        step=timedelta(days=int(raw["step_months"]) * day),
    )


def _default_gate(cfg: EngineConfig) -> ReplayGate:
    raw = cfg.get("backtest.paper_gate")
    return ReplayGate(
        min_trades=int(raw["min_aggregate_trades"]),
        min_profit_factor=Decimal(str(raw["min_oos_profit_factor"])),
        min_expectancy_r=Decimal(str(raw["min_oos_expectancy_r"])),
        max_top5_contribution=Decimal(str(raw["max_top5_contribution"])),
    )


def _blocked_run(
    session: Session,
    *,
    dataset: ResolvedDataset,
    strategy_version: str,
    cfg: EngineConfig,
    reason: str,
    detail: dict[str, object] | None = None,
) -> BacktestRun:
    period_from, period_to = _dataset_period(dataset)
    symbol = str(dataset.source_watermark.get("symbol") or "")
    payload = {
        "dataset": dataset.audit,
        "oos": {"folds": 0, "observations": 0},
        "reason": reason,
        **(detail or {}),
    }
    run = BacktestRun(
        label=f"bybit-backtest:{strategy_version}:{dataset.snapshot_id[:12]}",
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
        report_json=payload,
        gate_passed=False,
        gate_detail_json={
            "dataset_readiness": dataset.source_watermark.get("readiness"),
            "reason": reason,
        },
    )
    session.add(run)
    return run


def run_bybit_backtest(
    session: Session,
    *,
    store: FilesystemSnapshotStore,
    snapshot_id: str,
    strategy_version: str,
    evaluator: ReplayEvaluator,
    walk_forward: WalkForwardConfig | None = None,
    gate: ReplayGate | None = None,
    label_horizon: timedelta = timedelta(days=5),
    periods_per_year: Decimal = Decimal("365"),
    cfg: EngineConfig | None = None,
) -> BacktestRun:
    """Run OOS replay from exactly one immutable Bybit snapshot.

    ``evaluator`` must be a pure historical strategy adapter. It receives no
    HTTP client and no wall-clock market provider from this runner.
    """

    if not strategy_version.strip():
        raise ValueError("strategy_version is required")
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")
    config = cfg or get_config()
    dataset = DatasetSnapshotResolver(session, store=store).resolve_snapshot_id(snapshot_id)
    if not dataset.dataset_name.startswith("bybit:"):
        raise ValueError("Bybit runner requires a bybit:* dataset snapshot")

    readiness = str(dataset.source_watermark.get("readiness") or "")
    if readiness != DATA_READY:
        return _blocked_run(
            session,
            dataset=dataset,
            strategy_version=strategy_version,
            cfg=config,
            reason="DATA_BLOCKED",
        )

    wf = walk_forward or _default_walk_forward(config)
    threshold = gate or _default_gate(config)
    samples = _samples(dataset, label_horizon=label_horizon)
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

    observations: list[PathObservation] = []
    seen_times: set[datetime] = set()
    fold_evidence: list[dict[str, object]] = []
    for fold in folds:
        produced = tuple(evaluator(dataset, fold))
        allowed_times = {sample.observed_at for sample in fold.test}
        for observation in produced:
            if observation.at not in allowed_times:
                raise ValueError(
                    "evaluator returned an observation outside the OOS test fold"
                )
            if observation.at in seen_times:
                raise ValueError("OOS evaluator duplicated an observation timestamp")
            seen_times.add(observation.at)
            observations.append(observation)
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
                "oos_observations": len(produced),
                "purged": len(fold.purged_sample_ids),
                "embargoed": len(fold.embargoed_sample_ids),
            }
        )

    ordered = tuple(sorted(observations, key=lambda item: item.at))
    if not ordered:
        return _blocked_run(
            session,
            dataset=dataset,
            strategy_version=strategy_version,
            cfg=config,
            reason="NO_OOS_TRADES",
            detail={"folds": len(folds)},
        )

    robustness = compute_robustness_report(
        ordered,
        periods_per_year=periods_per_year,
    )
    top5 = _top5_contribution(ordered)
    profit_factor = (
        None
        if robustness.profit_factor is None
        else Decimal(str(robustness.profit_factor))
    )
    expectancy = Decimal(str(robustness.expectancy))
    criteria = {
        "min_trades": len(ordered) >= threshold.min_trades,
        "min_profit_factor": (
            profit_factor is not None and profit_factor >= threshold.min_profit_factor
        ),
        "min_expectancy_r": expectancy >= threshold.min_expectancy_r,
        "max_top5_contribution": top5 <= threshold.max_top5_contribution,
    }
    gate_passed = all(criteria.values())
    period_from, period_to = _dataset_period(dataset)
    symbol = str(dataset.source_watermark.get("symbol") or "")
    report = {
        "dataset": dataset.audit,
        "walk_forward": {
            "month_basis_days": 30 if walk_forward is None else None,
            "folds": fold_evidence,
        },
        "oos": {
            "folds": len(folds),
            "observations": len(ordered),
            "metrics": asdict(robustness),
            "top5_contribution": str(top5),
        },
    }
    gate_detail = {
        "dataset_readiness": readiness,
        "criteria": criteria,
        "thresholds": {
            "min_trades": threshold.min_trades,
            "min_profit_factor": str(threshold.min_profit_factor),
            "min_expectancy_r": str(threshold.min_expectancy_r),
            "max_top5_contribution": str(threshold.max_top5_contribution),
        },
    }
    run = BacktestRun(
        label=f"bybit-backtest:{strategy_version}:{dataset.snapshot_id[:12]}",
        strategy=strategy_version,
        period_from=period_from,
        period_to=period_to,
        config_hash=config.config_hash,
        engine_version=ENGINE_VERSION,
        universe_json=["CRYPTO", "BYBIT", symbol] if symbol else ["CRYPTO", "BYBIT"],
        trades=len(ordered),
        net_return=Decimal(str(robustness.net_total_return)),
        profit_factor=profit_factor,
        expectancy_r=expectancy,
        max_drawdown=Decimal(str(robustness.max_drawdown)),
        sharpe=None if robustness.sharpe is None else Decimal(str(robustness.sharpe)),
        sortino=None if robustness.sortino is None else Decimal(str(robustness.sortino)),
        calmar=None if robustness.calmar is None else Decimal(str(robustness.calmar)),
        brier_score=(
            None if robustness.brier_score is None else Decimal(str(robustness.brier_score))
        ),
        pbo=None,
        top5_contribution=top5,
        report_json=report,
        gate_passed=gate_passed,
        gate_detail_json=gate_detail,
    )
    session.add(run)
    return run


__all__ = [
    "ReplayEvaluator",
    "ReplayGate",
    "run_bybit_backtest",
]
