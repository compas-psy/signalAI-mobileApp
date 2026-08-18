"""Append-only registry of research search campaigns, variants and outcomes.

The registry preserves the denominator behind a selected backtest result.  A
promising variant therefore remains auditable as "best of N registered
variants" instead of being presented later as one independent experiment.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UuidPk, utcnow_column


class ResearchSearchCampaign(UuidPk, Base):
    __tablename__ = "research_search_campaigns"

    hypothesis_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_name: Mapped[str] = mapped_column(String(96), nullable=False)
    dataset_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_family: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    planned_variant_count: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint(
            "planned_variant_count > 0",
            name="planned_variant_count_positive",
        ),
        Index("ix_research_search_campaigns_hypothesis", "hypothesis_id"),
        Index("ix_research_search_campaigns_started", "started_at"),
    )


class ResearchTrial(UuidPk, Base):
    __tablename__ = "research_trials"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("research_search_campaigns.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    parameter_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parameter_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint("ordinal > 0", name="ordinal_positive"),
        UniqueConstraint(
            "campaign_id",
            "ordinal",
            name="uq_research_trials_campaign_ordinal",
        ),
        UniqueConstraint(
            "campaign_id",
            "parameter_hash",
            name="uq_research_trials_campaign_parameter_hash",
        ),
        Index("ix_research_trials_campaign", "campaign_id"),
    )


class ResearchTrialOutcome(UuidPk, Base):
    __tablename__ = "research_trial_outcomes"

    trial_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("research_trials.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(nullable=False)
    primary_metric: Mapped[Decimal | None] = mapped_column(
        Numeric(28, 12), nullable=True
    )
    outcome_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint(
            "status IN ('COMPLETED','FAILED','INVALID')",
            name="status_terminal",
        ),
        UniqueConstraint("trial_id", name="uq_research_trial_outcomes_trial_id"),
        Index("ix_research_trial_outcomes_completed", "completed_at"),
    )


__all__ = [
    "ResearchSearchCampaign",
    "ResearchTrial",
    "ResearchTrialOutcome",
]
