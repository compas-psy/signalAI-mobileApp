"""Strict paired champion/challenger evaluation for offline measurement.

The evaluator proves that control and candidate were judged on the same
opportunity context before delegating common statistics to the existing
measurement report. It has no scanner, risk, notification, lifecycle or broker
side effects.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Iterable

from ..measurement.report import (
    MeasurementDataset,
    StrategyMeasurementRecord,
    build_strategy_measurement_report,
)


@dataclass(frozen=True, slots=True)
class ArmObservation:
    """One arm's immutable decision/outcome for a shared opportunity."""

    opportunity_id: str
    instrument_id: str
    decision_at: datetime
    market_snapshot_hash: str
    cost_model_hash: str
    venue: str
    regime: str
    signal_emitted: bool
    net_r: Decimal | None
    confidence: Decimal | None = None
    label_usable: bool = True

    def __post_init__(self) -> None:
        if not self.opportunity_id.strip():
            raise ValueError("opportunity_id is required")
        if not self.instrument_id.strip():
            raise ValueError("instrument_id is required")
        if self.decision_at.tzinfo is None:
            raise ValueError("decision_at must be timezone-aware")
        _sha256_identity("market_snapshot_hash", self.market_snapshot_hash)
        _sha256_identity("cost_model_hash", self.cost_model_hash)
        if not self.venue.strip():
            raise ValueError("venue is required")
        if not self.regime.strip():
            raise ValueError("regime is required")
        if not self.signal_emitted and self.net_r is not None:
            raise ValueError("signal outcome cannot exist when no signal was emitted")
        if self.signal_emitted and self.label_usable and self.net_r is None:
            raise ValueError("usable emitted signal requires a signal outcome")
        if self.confidence is not None and not (
            Decimal(0) <= self.confidence <= Decimal(1)
        ):
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class PairedEvaluationResult:
    control_version: str
    candidate_version: str
    dataset: MeasurementDataset
    paired_sample_size: int
    paired_usable_sample_size: int
    sample_adequate: bool
    incremental_net_expectancy_r: float | None
    incremental_max_drawdown_r: float | None
    hit_rate_delta: float | None
    calibration_delta: float | None
    opportunity_overlap: float
    candidate_only_wins: int
    candidate_only_losses: int
    control_only_wins: int
    control_only_losses: int
    same_data_hash: str
    cost_model_hash: str
    measurement_report: dict


def _sha256_identity(name: str, value: str) -> None:
    if len(value) != 64 or any(
        char not in "0123456789abcdefABCDEF" for char in value
    ):
        raise ValueError(f"{name} must be a 64-character SHA-256 identity")


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _index(rows: Iterable[ArmObservation]) -> dict[str, ArmObservation]:
    indexed: dict[str, ArmObservation] = {}
    for row in rows:
        if row.opportunity_id in indexed:
            raise ValueError(f"duplicate opportunity: {row.opportunity_id}")
        indexed[row.opportunity_id] = row
    return indexed


def _require_same_context(control: ArmObservation, candidate: ArmObservation) -> None:
    if control.instrument_id != candidate.instrument_id:
        raise ValueError(f"instrument mismatch for {control.opportunity_id}")
    if _utc(control.decision_at) != _utc(candidate.decision_at):
        raise ValueError(f"decision timestamp mismatch for {control.opportunity_id}")
    if control.market_snapshot_hash != candidate.market_snapshot_hash:
        raise ValueError(f"market snapshot mismatch for {control.opportunity_id}")
    if control.cost_model_hash != candidate.cost_model_hash:
        raise ValueError(f"cost model mismatch for {control.opportunity_id}")
    if control.venue != candidate.venue:
        raise ValueError(f"venue mismatch for {control.opportunity_id}")
    if control.regime != candidate.regime:
        raise ValueError(f"regime mismatch for {control.opportunity_id}")


def _pair_usable(control: ArmObservation, candidate: ArmObservation) -> bool:
    def usable(row: ArmObservation) -> bool:
        if not row.label_usable:
            return False
        return not row.signal_emitted or row.net_r is not None

    return usable(control) and usable(candidate)


def _decision_return(row: ArmObservation) -> float | None:
    if not row.signal_emitted:
        return 0.0
    return float(row.net_r) if row.net_r is not None else None


def _same_data_hash(pairs: list[tuple[ArmObservation, ArmObservation]]) -> str:
    payload = [
        {
            "opportunity_id": control.opportunity_id,
            "instrument_id": control.instrument_id,
            "decision_at": _utc(control.decision_at).isoformat(),
            "market_snapshot_hash": control.market_snapshot_hash.lower(),
            "cost_model_hash": control.cost_model_hash.lower(),
            "venue": control.venue,
            "regime": control.regime,
        }
        for control, _candidate in pairs
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluate_paired(
    control_rows: Iterable[ArmObservation],
    candidate_rows: Iterable[ArmObservation],
    *,
    control_version: str,
    candidate_version: str,
    dataset: MeasurementDataset,
    min_sample: int = 30,
) -> PairedEvaluationResult:
    """Compare two strategy versions only after exact opportunity-context proof."""

    if not control_version.strip() or not candidate_version.strip():
        raise ValueError("control_version and candidate_version are required")
    if control_version == candidate_version:
        raise ValueError("control and candidate versions must differ")
    if min_sample < 1:
        raise ValueError("min_sample must be positive")
    if not isinstance(dataset, MeasurementDataset):
        dataset = MeasurementDataset(dataset)

    control = _index(control_rows)
    candidate = _index(candidate_rows)
    if not control:
        raise ValueError("paired evaluator requires at least one opportunity")
    if control.keys() != candidate.keys():
        raise ValueError("control and candidate must use the same opportunity universe")

    pairs = [(control[key], candidate[key]) for key in sorted(control)]
    for control_row, candidate_row in pairs:
        _require_same_context(control_row, candidate_row)

    cost_hashes = {
        row.cost_model_hash.lower() for pair in pairs for row in pair
    }
    if len(cost_hashes) != 1:
        raise ValueError(
            "paired evaluator requires a single cost model for the entire run"
        )
    cost_model_hash = next(iter(cost_hashes))

    measurement_rows: list[StrategyMeasurementRecord] = []
    for control_row, candidate_row in pairs:
        pair_usable = _pair_usable(control_row, candidate_row)
        for row, version in (
            (control_row, control_version),
            (candidate_row, candidate_version),
        ):
            measurement_rows.append(
                StrategyMeasurementRecord(
                    input_id=row.opportunity_id,
                    timestamp=row.decision_at,
                    dataset=dataset,
                    variant=version,
                    strategy=version,
                    instrument_id=row.instrument_id,
                    venue=row.venue,
                    regime=row.regime,
                    outcome_r=_decision_return(row),
                    confidence=(
                        float(row.confidence)
                        if row.confidence is not None
                        else None
                    ),
                    label_usable=pair_usable,
                    signal_emitted=row.signal_emitted,
                )
            )

    start = min(_utc(row.decision_at) for pair in pairs for row in pair)
    end = max(_utc(row.decision_at) for pair in pairs for row in pair) + timedelta(
        microseconds=1
    )
    report = build_strategy_measurement_report(
        measurement_rows,
        from_time=start,
        to_time=end,
        champion=control_version,
        candidate=candidate_version,
        min_sample=min_sample,
    )
    paired = report["comparison"]["datasets"][dataset.value]
    delta = paired["incremental_control_delta"]

    return PairedEvaluationResult(
        control_version=control_version,
        candidate_version=candidate_version,
        dataset=dataset,
        paired_sample_size=int(delta["paired_sample_size"]),
        paired_usable_sample_size=int(delta["paired_usable_sample_size"]),
        sample_adequate=bool(delta["sample_adequate"]),
        incremental_net_expectancy_r=delta["incremental_net_expectancy_r"],
        incremental_max_drawdown_r=delta["incremental_max_drawdown_r"],
        hit_rate_delta=delta["hit_rate_delta"],
        calibration_delta=delta["calibration_delta"],
        opportunity_overlap=float(delta["opportunity_overlap"]),
        candidate_only_wins=int(delta["candidate_only_wins"]),
        candidate_only_losses=int(delta["candidate_only_losses"]),
        control_only_wins=int(delta["control_only_wins"]),
        control_only_losses=int(delta["control_only_losses"]),
        same_data_hash=_same_data_hash(pairs),
        cost_model_hash=cost_model_hash,
        measurement_report=report,
    )


__all__ = ["ArmObservation", "PairedEvaluationResult", "evaluate_paired"]
