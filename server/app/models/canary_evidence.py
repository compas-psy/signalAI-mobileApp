"""Append-only metadata binding opaque Canary evidence refs to trusted server facts."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UuidPk, utcnow_column


class CanaryEvidenceReference(UuidPk, Base):
    __tablename__ = "canary_evidence_references"

    category: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    source_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    engine_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_family: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    venue: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fresh_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint(
            "category IN ('strategy_performance','shadow','testnet',"
            "'protection_reconciliation','kill_switch_drill','security_scan',"
            "'operational_health')",
            name="category_valid",
        ),
        CheckConstraint("verdict IN ('VERIFIED','FAILED')", name="verdict_valid"),
        CheckConstraint("char_length(evidence_ref) BETWEEN 1 AND 128", name="ref_non_empty"),
        CheckConstraint("char_length(source) BETWEEN 1 AND 64", name="source_non_empty"),
        CheckConstraint("char_length(artifact_sha256) = 64", name="artifact_sha256_width"),
        CheckConstraint("char_length(source_sha) = 40", name="source_sha_width"),
        CheckConstraint("char_length(engine_config_hash) = 64", name="config_hash_width"),
        CheckConstraint("fresh_until >= observed_at", name="freshness_ordered"),
        Index(
            "ix_canary_evidence_references_scope_category",
            "source_sha",
            "engine_config_hash",
            "strategy_family",
            "strategy_version",
            "venue",
            "category",
        ),
    )


__all__ = ["CanaryEvidenceReference"]
