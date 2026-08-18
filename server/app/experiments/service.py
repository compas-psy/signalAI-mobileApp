"""Transactional champion/challenger experiment evidence service.

This service records measurement evidence only. In particular,
``record_decision`` never changes ``StrategyRegistry`` roles or trading stages;
role promotion remains a separate audited governance action.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from ..models import (
    Experiment,
    ExperimentArm,
    ExperimentMetric,
    ExperimentRun,
    PromotionDecision,
)
from ..strategies.registry import StrategyRegistry
from ..strategies.versioning import StrategyRole, TradingStage


_DECISION_SOURCES = frozenset({"OWNER", "AUTOMATIC"})


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


def _normalise(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _normalise(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_normalise(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported experiment evidence value: {type(value).__name__}")


def _canonical_object(name: str, value: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a dict")
    normalised = _normalise(value)
    encoded = json.dumps(
        normalised,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return normalised, hashlib.sha256(encoded).hexdigest()


def _digest(name: str, value: str) -> None:
    if len(value) != 64:
        raise ValueError(f"{name} must be a 64-character SHA-256 identity")


def _stage(value: TradingStage | str) -> TradingStage:
    try:
        return value if isinstance(value, TradingStage) else TradingStage(value)
    except ValueError as exc:
        raise ValueError(f"unsupported experiment stage: {value}") from exc


class ExperimentService:
    """Persist immutable paired experiment evidence without runtime side effects."""

    def __init__(self, session: Session):
        self.session = session

    def create_experiment(
        self,
        *,
        name: str,
        control_family: str,
        control_version: str,
        candidate_family: str,
        candidate_version: str,
        dataset_name: str,
        dataset_snapshot_id: str,
        stage: TradingStage | str,
        same_data_hash: str,
        cost_model: dict[str, Any],
        created_at: datetime,
    ) -> Experiment:
        _aware("created_at", created_at)
        if not name.strip():
            raise ValueError("experiment name is required")
        if not dataset_name.strip():
            raise ValueError("dataset_name is required")
        _digest("dataset_snapshot_id", dataset_snapshot_id)
        _digest("same_data_hash", same_data_hash)
        resolved_stage = _stage(stage)
        if (control_family, control_version) == (
            candidate_family,
            candidate_version,
        ):
            raise ValueError("control and candidate must differ")

        registry = StrategyRegistry(self.session)
        try:
            control = registry.get(control_family, control_version)
        except KeyError as exc:
            raise KeyError(
                f"control strategy is not registered: {control_family}/{control_version}"
            ) from exc
        try:
            candidate = registry.get(candidate_family, candidate_version)
        except KeyError as exc:
            raise KeyError(
                f"candidate strategy is not registered: {candidate_family}/{candidate_version}"
            ) from exc
        if control.role is not StrategyRole.CONTROL:
            raise ValueError("control strategy must have CONTROL role")
        if candidate.role is StrategyRole.CONTROL:
            raise ValueError("candidate strategy must not have CONTROL role")

        normalised_costs, cost_hash = _canonical_object("cost_model", cost_model)
        row = Experiment(
            name=name.strip(),
            control_family=control.family,
            control_version=control.version,
            candidate_family=candidate.family,
            candidate_version=candidate.version,
            dataset_name=dataset_name.strip(),
            dataset_snapshot_id=dataset_snapshot_id,
            stage=resolved_stage.value,
            same_data_hash=same_data_hash,
            cost_model_hash=cost_hash,
            cost_model_json=normalised_costs,
            created_at=created_at,
        )
        self.session.add(row)
        self.session.flush()
        self.session.add_all(
            [
                ExperimentArm(
                    experiment_id=row.id,
                    arm_role="CONTROL",
                    strategy_family=control.family,
                    strategy_version=control.version,
                ),
                ExperimentArm(
                    experiment_id=row.id,
                    arm_role="CANDIDATE",
                    strategy_family=candidate.family,
                    strategy_version=candidate.version,
                ),
            ]
        )
        self.session.flush()
        return row

    def record_run(
        self,
        *,
        experiment_id: uuid.UUID,
        dataset_snapshot_id: str,
        stage: TradingStage | str,
        same_data_hash: str,
        cost_model: dict[str, Any],
        sample_size: int,
        sample_adequate: bool,
        result: dict[str, Any],
        evaluated_at: datetime,
    ) -> ExperimentRun:
        _aware("evaluated_at", evaluated_at)
        row = self.session.get(Experiment, experiment_id)
        if row is None:
            raise KeyError(f"experiment {experiment_id} not found")
        if dataset_snapshot_id != row.dataset_snapshot_id:
            raise ValueError("dataset snapshot differs from experiment evidence")
        resolved_stage = _stage(stage)
        if resolved_stage.value != row.stage:
            raise ValueError("stage differs from experiment evidence")
        if same_data_hash != row.same_data_hash:
            raise ValueError("same-data proof differs from experiment evidence")
        normalised_costs, cost_hash = _canonical_object("cost_model", cost_model)
        if cost_hash != row.cost_model_hash:
            raise ValueError("cost model differs from experiment evidence")
        if sample_size < 0:
            raise ValueError("sample_size must be non-negative")
        if not isinstance(sample_adequate, bool):
            raise TypeError("sample_adequate must be bool")
        normalised_result, _ = _canonical_object("result", result)
        if evaluated_at < row.created_at:
            raise ValueError("evaluated_at cannot precede experiment creation")

        run = ExperimentRun(
            experiment_id=row.id,
            dataset_snapshot_id=dataset_snapshot_id,
            stage=resolved_stage.value,
            same_data_hash=same_data_hash,
            cost_model_hash=cost_hash,
            cost_model_json=normalised_costs,
            result_json=normalised_result,
            sample_size=sample_size,
            sample_adequate=sample_adequate,
            evaluated_at=evaluated_at,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def record_metric(
        self,
        run_id: uuid.UUID,
        *,
        name: str,
        control_value: Decimal,
        candidate_value: Decimal,
        unit: str | None,
        recorded_at: datetime,
    ) -> ExperimentMetric:
        _aware("recorded_at", recorded_at)
        run = self.session.get(ExperimentRun, run_id)
        if run is None:
            raise KeyError(f"experiment run {run_id} not found")
        if not name.strip():
            raise ValueError("metric name is required")
        if recorded_at < run.evaluated_at:
            raise ValueError("recorded_at cannot precede run evaluation")
        metric = ExperimentMetric(
            run_id=run.id,
            name=name.strip(),
            control_value=control_value,
            candidate_value=candidate_value,
            delta=candidate_value - control_value,
            unit=unit.strip() if unit is not None else None,
            recorded_at=recorded_at,
        )
        self.session.add(metric)
        self.session.flush()
        return metric

    def record_decision(
        self,
        *,
        experiment_id: uuid.UUID,
        run_id: uuid.UUID,
        decision: str,
        source: str,
        actor: str,
        reason: str,
        decided_at: datetime,
        detail: dict[str, Any],
    ) -> PromotionDecision:
        """Record a recommendation/owner decision without mutating registry state."""

        _aware("decided_at", decided_at)
        experiment = self.session.get(Experiment, experiment_id)
        if experiment is None:
            raise KeyError(f"experiment {experiment_id} not found")
        run = self.session.get(ExperimentRun, run_id)
        if run is None or run.experiment_id != experiment.id:
            raise ValueError("run does not belong to experiment")
        if not decision.strip():
            raise ValueError("decision is required")
        resolved_source = source.upper()
        if resolved_source not in _DECISION_SOURCES:
            raise ValueError(f"source must be one of {sorted(_DECISION_SOURCES)}")
        if not actor.strip():
            raise ValueError("actor is required")
        if not reason.strip():
            raise ValueError("reason is required")
        if decided_at < run.evaluated_at:
            raise ValueError("decided_at cannot precede run evaluation")
        normalised_detail, _ = _canonical_object("detail", detail)

        row = PromotionDecision(
            experiment_id=experiment.id,
            run_id=run.id,
            decision=decision.strip(),
            source=resolved_source,
            actor=actor.strip(),
            reason=reason.strip(),
            detail_json=normalised_detail,
            decided_at=decided_at,
        )
        self.session.add(row)
        self.session.flush()
        return row


__all__ = ["ExperimentService"]
