"""Reproducible strategy measurement over normalized outcome records.

This module intentionally contains no strategy or execution decisions. It only
turns a fixed set of already-observed outcomes into one stable report shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import floor
from statistics import fmean
from typing import Callable, Iterable


class MeasurementDataset(StrEnum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    SANDBOX = "SANDBOX"
    LIVE = "LIVE"


@dataclass(frozen=True)
class StrategyMeasurementRecord:
    """One immutable opportunity/outcome used by the measurement contract."""

    input_id: str
    timestamp: datetime
    dataset: MeasurementDataset
    variant: str
    strategy: str
    instrument_id: str
    venue: str
    regime: str
    outcome_r: float | None
    mfe_r: float | None = None
    mae_r: float | None = None
    entry_deviation_r: float | None = None
    exit_deviation_r: float | None = None
    confidence: float | None = None
    operational_failure: bool = False
    reconciliation_mismatch: bool = False
    label_usable: bool = True


_DATASETS = tuple(MeasurementDataset)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _clean_number(value: float | None) -> float | None:
    if value is None:
        return None
    # Stable JSON without inventing display precision. Ten decimals is far
    # below the stored NUMERIC precision used for R metrics and avoids tiny
    # binary-float tails changing snapshots.
    return round(float(value), 10)


def _mean(values: Iterable[float | None], *, absolute: bool = False) -> float | None:
    cleaned = [
        abs(float(value)) if absolute else float(value)
        for value in values
        if value is not None
    ]
    return _clean_number(fmean(cleaned)) if cleaned else None


def _drawdown_and_recovery(records: list[StrategyMeasurementRecord]) -> tuple[float | None, int | None]:
    usable = [record for record in records if record.label_usable and record.outcome_r is not None]
    if not usable:
        return None, None

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    recovery_trades = 0
    max_recovery_trades = 0
    recovering = False

    for record in usable:
        equity += float(record.outcome_r)
        if equity >= peak:
            if recovering:
                recovery_trades += 1  # recovery trade itself counts
                max_recovery_trades = max(max_recovery_trades, recovery_trades)
            peak = equity
            recovering = False
            recovery_trades = 0
            continue

        drawdown = peak - equity
        max_drawdown = max(max_drawdown, drawdown)
        if recovering:
            recovery_trades += 1
        else:
            recovering = True
            recovery_trades = 1
        max_recovery_trades = max(max_recovery_trades, recovery_trades)

    return _clean_number(max_drawdown), max_recovery_trades


def _calibration(records: list[StrategyMeasurementRecord]) -> list[dict]:
    buckets: dict[int, list[StrategyMeasurementRecord]] = {}
    for record in records:
        if not record.label_usable or record.outcome_r is None or record.confidence is None:
            continue
        confidence = min(1.0, max(0.0, float(record.confidence)))
        index = min(9, max(0, floor(confidence * 10)))
        buckets.setdefault(index, []).append(record)

    result: list[dict] = []
    for index in sorted(buckets):
        bucket = buckets[index]
        mean_confidence = fmean(float(record.confidence) for record in bucket)
        observed = fmean(1.0 if float(record.outcome_r) > 0 else 0.0 for record in bucket)
        result.append(
            {
                "bucket": f"{index / 10:.1f}-{(index + 1) / 10:.1f}",
                "count": len(bucket),
                "mean_confidence": _clean_number(mean_confidence),
                "observed_win_rate": _clean_number(observed),
                "absolute_error": _clean_number(abs(mean_confidence - observed)),
            }
        )
    return result


def _metrics(records: list[StrategyMeasurementRecord], *, min_sample: int) -> dict:
    ordered = sorted(
        records,
        key=lambda record: (_utc(record.timestamp), record.dataset.value, record.input_id),
    )
    usable = [record for record in ordered if record.label_usable and record.outcome_r is not None]
    outcomes = [float(record.outcome_r) for record in usable]
    max_drawdown, max_recovery = _drawdown_and_recovery(ordered)
    total = len(ordered)

    return {
        "total_records": total,
        "usable_sample_size": len(usable),
        "unusable_label_count": total - len(usable),
        "expectancy_r": _clean_number(fmean(outcomes)) if outcomes else None,
        "win_rate": (
            _clean_number(fmean(1.0 if outcome > 0 else 0.0 for outcome in outcomes))
            if outcomes
            else None
        ),
        "avg_mfe_r": _mean((record.mfe_r for record in usable)),
        "avg_mae_r": _mean((record.mae_r for record in usable)),
        "avg_entry_deviation_r": _mean(
            (record.entry_deviation_r for record in usable), absolute=True
        ),
        "avg_exit_deviation_r": _mean(
            (record.exit_deviation_r for record in usable), absolute=True
        ),
        "max_drawdown_r": max_drawdown,
        "max_recovery_trades": max_recovery,
        "confidence_calibration": _calibration(usable),
        "operational_failure_rate": (
            _clean_number(sum(record.operational_failure for record in ordered) / total)
            if total
            else None
        ),
        "reconciliation_mismatch_rate": (
            _clean_number(sum(record.reconciliation_mismatch for record in ordered) / total)
            if total
            else None
        ),
        "sufficient_sample": len(usable) >= min_sample,
    }


def _datasets(records: list[StrategyMeasurementRecord], *, min_sample: int) -> dict[str, dict]:
    return {
        dataset.value: _metrics(
            [record for record in records if record.dataset is dataset],
            min_sample=min_sample,
        )
        for dataset in _DATASETS
    }


def _grouped(
    records: list[StrategyMeasurementRecord],
    key: Callable[[StrategyMeasurementRecord], str],
    *,
    min_sample: int,
) -> list[dict]:
    grouped: dict[str, list[StrategyMeasurementRecord]] = {}
    for record in records:
        grouped.setdefault(key(record) or "UNKNOWN", []).append(record)
    return [
        {"key": name, "datasets": _datasets(grouped[name], min_sample=min_sample)}
        for name in sorted(grouped)
    ]


def _variant_report(records: list[StrategyMeasurementRecord], *, min_sample: int) -> dict:
    return {
        "datasets": _datasets(records, min_sample=min_sample),
        "by_strategy": _grouped(records, lambda record: record.strategy, min_sample=min_sample),
        "by_instrument": _grouped(
            records, lambda record: record.instrument_id, min_sample=min_sample
        ),
        "by_venue": _grouped(records, lambda record: record.venue, min_sample=min_sample),
        "by_regime": _grouped(records, lambda record: record.regime, min_sample=min_sample),
    }


def _comparison(
    records: list[StrategyMeasurementRecord],
    *,
    champion: str,
    candidate: str,
    min_sample: int,
) -> dict:
    result: dict[str, dict] = {}
    for dataset in _DATASETS:
        champion_rows = {
            record.input_id: record
            for record in records
            if record.variant == champion and record.dataset is dataset
        }
        candidate_rows = {
            record.input_id: record
            for record in records
            if record.variant == candidate and record.dataset is dataset
        }
        common = sorted(champion_rows.keys() & candidate_rows.keys())
        champion_paired = [champion_rows[input_id] for input_id in common]
        candidate_paired = [candidate_rows[input_id] for input_id in common]
        champion_metrics = _metrics(champion_paired, min_sample=min_sample)
        candidate_metrics = _metrics(candidate_paired, min_sample=min_sample)
        champion_expectancy = champion_metrics["expectancy_r"]
        candidate_expectancy = candidate_metrics["expectancy_r"]
        paired_usable = min(
            champion_metrics["usable_sample_size"],
            candidate_metrics["usable_sample_size"],
        )

        result[dataset.value] = {
            "paired_sample_size": len(common),
            "paired_usable_sample_size": paired_usable,
            "champion_only_count": len(champion_rows.keys() - candidate_rows.keys()),
            "candidate_only_count": len(candidate_rows.keys() - champion_rows.keys()),
            "champion": champion_metrics,
            "candidate": candidate_metrics,
            "delta_expectancy_r": (
                _clean_number(candidate_expectancy - champion_expectancy)
                if champion_expectancy is not None and candidate_expectancy is not None
                else None
            ),
            "comparable": paired_usable >= min_sample,
        }

    return {
        "champion": champion,
        "candidate": candidate,
        "datasets": result,
    }


def build_strategy_measurement_report(
    records: Iterable[StrategyMeasurementRecord],
    *,
    from_time: datetime,
    to_time: datetime,
    champion: str,
    candidate: str,
    min_sample: int = 30,
    unclassified_count: int = 0,
) -> dict:
    """Build one stable report for a half-open UTC period.

    The comparison is deliberately paired on the same `(dataset, input_id)`
    opportunity. Candidate-only wins or champion-only losses can therefore not
    improve the reported head-to-head result.
    """

    start = _utc(from_time)
    end = _utc(to_time)
    if start >= end:
        raise ValueError("measurement period must satisfy from_time < to_time")
    if min_sample < 1:
        raise ValueError("min_sample must be positive")
    if not champion or not candidate:
        raise ValueError("champion and candidate must be non-empty")

    filtered = [
        record
        for record in records
        if start <= _utc(record.timestamp) < end
    ]
    filtered.sort(
        key=lambda record: (
            _utc(record.timestamp),
            record.dataset.value,
            record.input_id,
            record.variant,
        )
    )

    seen: set[tuple[str, MeasurementDataset, str]] = set()
    for record in filtered:
        identity = (record.variant, record.dataset, record.input_id)
        if identity in seen:
            raise ValueError(
                "duplicate measurement record: "
                f"variant={record.variant} dataset={record.dataset.value} "
                f"input_id={record.input_id}"
            )
        seen.add(identity)

    variants = {
        variant: _variant_report(
            [record for record in filtered if record.variant == variant],
            min_sample=min_sample,
        )
        for variant in dict.fromkeys((champion, candidate))
    }

    return {
        "period": {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "closed": "[from,to)",
        },
        "min_sample": min_sample,
        "unclassified_count": int(unclassified_count),
        "variants": variants,
        "comparison": _comparison(
            filtered,
            champion=champion,
            candidate=candidate,
            min_sample=min_sample,
        ),
    }


__all__ = [
    "MeasurementDataset",
    "StrategyMeasurementRecord",
    "build_strategy_measurement_report",
]
