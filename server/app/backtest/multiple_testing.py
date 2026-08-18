"""Append-only guardrail against hidden multiple testing in strategy research.

A search campaign predeclares its variant count and immutable dataset/strategy
identity. Parameter sets are registered before outcomes, failures remain in the
denominator, and selection evidence is always explicitly labelled ``best_of_N``.

This module is offline research governance only. It does not gate or alter the
production scanner, paper lifecycle, risk or execution paths.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ResearchSearchCampaign, ResearchTrial, ResearchTrialOutcome


_TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "INVALID"})


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
    raise TypeError(f"unsupported research registry value: {type(value).__name__}")


def _canonical_json(value: Any) -> tuple[Any, bytes]:
    normalised = _normalise(value)
    encoded = json.dumps(
        normalised,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return normalised, encoded


def _parameter_hash(parameters: dict[str, Any]) -> tuple[dict[str, Any], str]:
    normalised, encoded = _canonical_json(parameters)
    if not isinstance(normalised, dict):
        raise TypeError("parameters must normalise to an object")
    return normalised, hashlib.sha256(encoded).hexdigest()


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class SelectionEvidence:
    campaign_id: uuid.UUID
    hypothesis_id: str
    dataset_name: str
    dataset_snapshot_id: str
    strategy_family: str
    strategy_version: str
    planned_variants: int
    registered_variants: int
    terminal_variants: int
    completed_variants: int
    failed_variants: int
    best_trial_id: uuid.UUID | None
    best_primary_metric: Decimal | None
    selection_context: str
    blockers: tuple[str, ...]
    promotion_ready: bool


class MultipleTestingRegistry:
    """Transactional API for predeclared research searches and trial outcomes."""

    def __init__(self, session: Session):
        self.session = session

    def create_campaign(
        self,
        *,
        hypothesis_id: str,
        dataset_name: str,
        dataset_snapshot_id: str,
        strategy_family: str,
        strategy_version: str,
        config_hash: str,
        planned_variant_count: int,
        started_at: datetime,
    ) -> ResearchSearchCampaign:
        _aware("started_at", started_at)
        for name, value in (
            ("hypothesis_id", hypothesis_id),
            ("dataset_name", dataset_name),
            ("dataset_snapshot_id", dataset_snapshot_id),
            ("strategy_family", strategy_family),
            ("strategy_version", strategy_version),
            ("config_hash", config_hash),
        ):
            if not value.strip():
                raise ValueError(f"{name} is required")
        if len(dataset_snapshot_id) != 64:
            raise ValueError("dataset_snapshot_id must be a 64-char snapshot identity")
        if len(config_hash) != 64:
            raise ValueError("config_hash must be a 64-char SHA-256")
        if planned_variant_count <= 0:
            raise ValueError("planned_variant_count must be positive")

        campaign = ResearchSearchCampaign(
            hypothesis_id=hypothesis_id,
            dataset_name=dataset_name,
            dataset_snapshot_id=dataset_snapshot_id,
            strategy_family=strategy_family,
            strategy_version=strategy_version,
            config_hash=config_hash,
            planned_variant_count=planned_variant_count,
            started_at=started_at,
        )
        self.session.add(campaign)
        self.session.flush()
        return campaign

    def _locked_campaign(self, campaign_id: uuid.UUID) -> ResearchSearchCampaign:
        campaign = self.session.execute(
            select(ResearchSearchCampaign)
            .where(ResearchSearchCampaign.id == campaign_id)
            .with_for_update()
        ).scalar_one_or_none()
        if campaign is None:
            raise KeyError(f"research campaign {campaign_id} not found")
        return campaign

    def start_trial(
        self,
        campaign_id: uuid.UUID,
        *,
        parameters: dict[str, Any],
        started_at: datetime,
    ) -> ResearchTrial:
        _aware("started_at", started_at)
        if not isinstance(parameters, dict):
            raise TypeError("parameters must be a dict")
        normalised, parameter_hash = _parameter_hash(parameters)
        campaign = self._locked_campaign(campaign_id)
        if started_at < campaign.started_at:
            raise ValueError("trial started_at cannot precede campaign started_at")

        existing = self.session.execute(
            select(ResearchTrial).where(
                ResearchTrial.campaign_id == campaign_id,
                ResearchTrial.parameter_hash == parameter_hash,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        registered = self.session.execute(
            select(func.count(ResearchTrial.id)).where(
                ResearchTrial.campaign_id == campaign_id
            )
        ).scalar_one()
        if registered >= campaign.planned_variant_count:
            raise ValueError(
                "planned_variant_count exhausted; create a new campaign instead of "
                "silently enlarging the search space"
            )

        trial = ResearchTrial(
            campaign_id=campaign_id,
            ordinal=int(registered) + 1,
            parameter_hash=parameter_hash,
            parameter_json=normalised,
            started_at=started_at,
        )
        self.session.add(trial)
        self.session.flush()
        return trial

    def record_outcome(
        self,
        trial_id: uuid.UUID,
        *,
        status: str,
        completed_at: datetime,
        primary_metric: Decimal | None,
        outcome: dict[str, Any],
    ) -> ResearchTrialOutcome:
        _aware("completed_at", completed_at)
        status = status.upper()
        if status not in _TERMINAL_STATUSES:
            raise ValueError(f"status must be one of {sorted(_TERMINAL_STATUSES)}")
        trial = self.session.get(ResearchTrial, trial_id)
        if trial is None:
            raise KeyError(f"research trial {trial_id} not found")
        if completed_at < trial.started_at:
            raise ValueError("completed_at cannot precede trial started_at")
        existing = self.session.execute(
            select(ResearchTrialOutcome).where(
                ResearchTrialOutcome.trial_id == trial_id
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ValueError("trial already has an outcome; outcomes are immutable")
        if status == "COMPLETED" and primary_metric is None:
            raise ValueError("COMPLETED trial requires primary_metric")
        if status != "COMPLETED" and primary_metric is not None:
            raise ValueError("non-COMPLETED trial must not carry a primary_metric")
        if not isinstance(outcome, dict):
            raise TypeError("outcome must be a dict")
        normalised, _ = _canonical_json(outcome)

        row = ResearchTrialOutcome(
            trial_id=trial_id,
            status=status,
            completed_at=completed_at,
            primary_metric=primary_metric,
            outcome_json=normalised,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def selection_evidence(self, campaign_id: uuid.UUID) -> SelectionEvidence:
        campaign = self.session.get(ResearchSearchCampaign, campaign_id)
        if campaign is None:
            raise KeyError(f"research campaign {campaign_id} not found")

        rows = self.session.execute(
            select(ResearchTrial, ResearchTrialOutcome)
            .outerjoin(
                ResearchTrialOutcome,
                ResearchTrialOutcome.trial_id == ResearchTrial.id,
            )
            .where(ResearchTrial.campaign_id == campaign_id)
            .order_by(ResearchTrial.ordinal.asc())
        ).all()
        registered = len(rows)
        outcomes = [outcome for _, outcome in rows if outcome is not None]
        terminal = len(outcomes)
        completed = [
            (trial, outcome)
            for trial, outcome in rows
            if outcome is not None and outcome.status == "COMPLETED"
        ]
        failed = sum(
            1
            for outcome in outcomes
            if outcome.status in {"FAILED", "INVALID"}
        )

        best_trial_id: uuid.UUID | None = None
        best_metric: Decimal | None = None
        if completed:
            best_trial, best_outcome = max(
                completed,
                key=lambda pair: pair[1].primary_metric,
            )
            best_trial_id = best_trial.id
            best_metric = best_outcome.primary_metric

        blockers: list[str] = []
        if registered != campaign.planned_variant_count:
            blockers.append(
                f"registered {registered}/{campaign.planned_variant_count}"
            )
        if terminal != registered:
            blockers.append(f"terminal outcomes {terminal}/{registered}")
        if not completed:
            blockers.append("no completed variants")

        if registered == campaign.planned_variant_count:
            context = f"best_of_{registered}_registered_variants"
        else:
            context = (
                f"best_of_{registered}_registered_variants;"
                f"planned={campaign.planned_variant_count}"
            )

        return SelectionEvidence(
            campaign_id=campaign.id,
            hypothesis_id=campaign.hypothesis_id,
            dataset_name=campaign.dataset_name,
            dataset_snapshot_id=campaign.dataset_snapshot_id,
            strategy_family=campaign.strategy_family,
            strategy_version=campaign.strategy_version,
            planned_variants=campaign.planned_variant_count,
            registered_variants=registered,
            terminal_variants=terminal,
            completed_variants=len(completed),
            failed_variants=failed,
            best_trial_id=best_trial_id,
            best_primary_metric=best_metric,
            selection_context=context,
            blockers=tuple(blockers),
            promotion_ready=not blockers,
        )


__all__ = ["MultipleTestingRegistry", "SelectionEvidence"]
