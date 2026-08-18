"""Persistent champion/challenger experiment evidence.

These tables are measurement/governance state only. They do not select runtime
strategies, admit signals, alter risk, or submit broker orders. Experiment
history is append-only so a later decision cannot rewrite the evidence it was
based on.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Timestamp, UuidPk, utcnow_column


class Experiment(UuidPk, Base):
    __tablename__ = "experiments"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    control_family: Mapped[str] = mapped_column(String(32), nullable=False)
    control_version: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_family: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_version: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_name: Mapped[str] = mapped_column(String(96), nullable=False)
    dataset_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    same_data_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cost_model_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cost_model_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[Timestamp] = mapped_column(nullable=False)
    persisted_at: Mapped[datetime] = utcnow_column()

    arms: Mapped[list["ExperimentArm"]] = relationship(
        back_populates="experiment", order_by="ExperimentArm.arm_role", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint(
            "NOT (control_family = candidate_family AND control_version = candidate_version)",
            name="control_candidate_differ",
        ),
        Index("ix_experiments_created_at", "created_at"),
        Index("ix_experiments_stage", "stage"),
    )


class ExperimentArm(UuidPk, Base):
    __tablename__ = "experiment_arms"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("experiments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    arm_role: Mapped[str] = mapped_column(String(16), nullable=False)
    strategy_family: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    experiment: Mapped[Experiment] = relationship(back_populates="arms")

    __table_args__ = (
        CheckConstraint(
            "arm_role IN ('CONTROL','CANDIDATE')", name="arm_role_valid"
        ),
        UniqueConstraint(
            "experiment_id", "arm_role", name="uq_experiment_arms_experiment_role"
        ),
        UniqueConstraint(
            "experiment_id",
            "strategy_family",
            "strategy_version",
            name="uq_experiment_arms_strategy",
        ),
        Index("ix_experiment_arms_experiment", "experiment_id"),
    )


class ExperimentRun(UuidPk, Base):
    __tablename__ = "experiment_runs"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("experiments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    dataset_snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    same_data_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cost_model_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cost_model_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_adequate: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evaluated_at: Mapped[Timestamp] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint("sample_size >= 0", name="sample_size_non_negative"),
        Index("ix_experiment_runs_experiment", "experiment_id"),
        Index("ix_experiment_runs_evaluated", "evaluated_at"),
    )


class ExperimentMetric(UuidPk, Base):
    __tablename__ = "experiment_metrics"

    run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("experiment_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    control_value: Mapped[Decimal] = mapped_column(Numeric(28, 12), nullable=False)
    candidate_value: Mapped[Decimal] = mapped_column(Numeric(28, 12), nullable=False)
    delta: Mapped[Decimal] = mapped_column(Numeric(28, 12), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(24), nullable=True)
    recorded_at: Mapped[Timestamp] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        UniqueConstraint("run_id", "name", name="uq_experiment_metrics_run_name"),
        Index("ix_experiment_metrics_run", "run_id"),
    )


class PromotionDecision(UuidPk, Base):
    __tablename__ = "promotion_decisions"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("experiments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("experiment_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    detail_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    decided_at: Mapped[Timestamp] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint("source IN ('OWNER','AUTOMATIC')", name="source_valid"),
        Index("ix_promotion_decisions_experiment", "experiment_id"),
        Index("ix_promotion_decisions_decided", "decided_at"),
    )


__all__ = [
    "Experiment",
    "ExperimentArm",
    "ExperimentMetric",
    "ExperimentRun",
    "PromotionDecision",
]
