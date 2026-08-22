"""Append-only promotion evidence and decision correlations (B5 / R6)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UuidPk, utcnow_column


class PromotionEvidenceSnapshot(UuidPk, Base):
    """One immutable, versioned fact emitted by a trusted server measurement."""

    __tablename__ = "promotion_evidence_snapshots"

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_family: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    venue: Mapped[str] = mapped_column(String(32), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fresh_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False)
    gate_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reconciliation_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    protection_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    kill_switch_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    capability: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint(
            "kind IN ('TECHNICAL', 'PERFORMANCE', 'OPERATIONS')", name="kind_valid"
        ),
        CheckConstraint("evidence_version > 0", name="version_positive"),
        CheckConstraint("sample_size >= 0", name="sample_non_negative"),
        CheckConstraint(
            "error_count >= 0 AND error_count <= sample_size", name="error_in_sample"
        ),
        CheckConstraint("fresh_until >= observed_at", name="freshness_ordered"),
        CheckConstraint("char_length(source_hash) = 64", name="source_hash_sha256_width"),
        CheckConstraint("char_length(config_hash) = 64", name="config_hash_sha256_width"),
        CheckConstraint("char_length(policy_hash) = 64", name="policy_hash_sha256_width"),
        Index(
            "ix_promotion_evidence_snapshots_scope",
            "strategy_family",
            "strategy_version",
            "venue",
            "kind",
            "observed_at",
        ),
    )


class PromotionEvidenceDecision(UuidPk, Base):
    """Append-only, non-secret correlation for a promotion or demotion decision."""

    __tablename__ = "promotion_evidence_decisions"

    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    current_mode: Mapped[str] = mapped_column(String(12), nullable=False)
    target_mode: Mapped[str] = mapped_column(String(12), nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    blockers_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    evidence_snapshot_ids_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    strategy_family: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    venue: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint("char_length(correlation_id) > 0", name="correlation_non_empty"),
        Index(
            "uq_promotion_evidence_decisions_correlation",
            "correlation_id",
            unique=True,
        ),
        Index("ix_promotion_evidence_decisions_occurred", "occurred_at"),
    )


__all__ = ["PromotionEvidenceDecision", "PromotionEvidenceSnapshot"]
