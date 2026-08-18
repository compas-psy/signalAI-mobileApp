"""Read-only champion/challenger experiment evidence API.

The endpoints expose persisted SAI-010/012 evidence for the owner UI. They do
not execute experiments, mutate strategy roles, or perform promotion.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Experiment, ExperimentMetric, ExperimentRun, PromotionDecision


router = APIRouter(prefix="/experiments", tags=["experiments"])


def _number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _latest_run(db: Session, experiment_id: UUID) -> ExperimentRun | None:
    return db.execute(
        select(ExperimentRun)
        .where(ExperimentRun.experiment_id == experiment_id)
        .order_by(ExperimentRun.evaluated_at.desc(), ExperimentRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_decision(db: Session, experiment_id: UUID) -> PromotionDecision | None:
    return db.execute(
        select(PromotionDecision)
        .where(PromotionDecision.experiment_id == experiment_id)
        .order_by(PromotionDecision.decided_at.desc(), PromotionDecision.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _list_summary(experiment: Experiment, run: ExperimentRun | None) -> dict:
    return {
        "id": str(experiment.id),
        "name": experiment.name,
        "control": {
            "family": experiment.control_family,
            "version": experiment.control_version,
        },
        "candidate": {
            "family": experiment.candidate_family,
            "version": experiment.candidate_version,
        },
        "stage": experiment.stage,
        "dataset_name": experiment.dataset_name,
        "created_at": experiment.created_at.isoformat(),
        "latest_run": (
            {
                "id": str(run.id),
                "evaluated_at": run.evaluated_at.isoformat(),
                "sample_size": run.sample_size,
                "sample_adequate": run.sample_adequate,
            }
            if run is not None
            else None
        ),
    }


@router.get("")
def list_experiments(
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict]:
    """List persisted experiments newest first without changing their state."""

    experiments = db.execute(
        select(Experiment)
        .order_by(Experiment.created_at.desc(), Experiment.id.desc())
        .limit(limit)
    ).scalars().all()
    return [
        _list_summary(experiment, _latest_run(db, experiment.id))
        for experiment in experiments
    ]


@router.get("/{experiment_id}/comparison")
def experiment_comparison(
    experiment_id: UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Return the latest persisted paired evidence for one experiment."""

    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise HTTPException(status_code=404, detail="experiment not found")

    run = _latest_run(db, experiment.id)
    metrics: list[ExperimentMetric] = []
    if run is not None:
        metrics = db.execute(
            select(ExperimentMetric)
            .where(ExperimentMetric.run_id == run.id)
            .order_by(ExperimentMetric.name, ExperimentMetric.recorded_at)
        ).scalars().all()
    decision = _latest_decision(db, experiment.id)

    return {
        "experiment": {
            "id": str(experiment.id),
            "name": experiment.name,
            "control_family": experiment.control_family,
            "control_version": experiment.control_version,
            "candidate_family": experiment.candidate_family,
            "candidate_version": experiment.candidate_version,
            "created_at": experiment.created_at.isoformat(),
        },
        "evidence": {
            "dataset_name": experiment.dataset_name,
            "dataset_snapshot_id": experiment.dataset_snapshot_id,
            "stage": experiment.stage,
            "same_data_hash": experiment.same_data_hash,
            "cost_model_hash": experiment.cost_model_hash,
        },
        "latest_run": (
            {
                "id": str(run.id),
                "evaluated_at": run.evaluated_at.isoformat(),
                "sample_size": run.sample_size,
                "sample_adequate": run.sample_adequate,
                "result": run.result_json,
            }
            if run is not None
            else None
        ),
        "metrics": [
            {
                "name": metric.name,
                "control_value": _number(metric.control_value),
                "candidate_value": _number(metric.candidate_value),
                "delta": _number(metric.delta),
                "unit": metric.unit,
                "recorded_at": metric.recorded_at.isoformat(),
            }
            for metric in metrics
        ],
        "latest_decision": (
            {
                "decision": decision.decision,
                "source": decision.source,
                "actor": decision.actor,
                "reason": decision.reason,
                "detail": decision.detail_json,
                "decided_at": decision.decided_at.isoformat(),
            }
            if decision is not None
            else None
        ),
    }


__all__ = ["router"]
