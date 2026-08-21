"""Append-only Shadow candidate measurement evidence.

Shadow observations are intentionally disconnected from ``TradeIdea``, paper
trades and execution tables. They are measurement facts only: enough to pair
candidate strategies on the same opportunity later, but incapable of creating
an executable lifecycle by relationship or foreign key.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamp, UuidPk, utcnow_column


class ShadowObservation(UuidPk, Base):
    __tablename__ = "shadow_observations"

    observation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    opportunity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(64), nullable=False)
    venue: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_family: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signal_emitted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    raw_edge_score: Mapped[Decimal | None] = mapped_column(
        Numeric(28, 12), nullable=True
    )
    entry_reference: Mapped[Decimal | None] = mapped_column(
        Numeric(28, 12), nullable=True
    )
    data_quality_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    evaluated_at: Mapped[Timestamp] = mapped_column(nullable=False)
    market_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cost_model_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    persisted_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        UniqueConstraint("observation_key", name="uq_shadow_observations_key"),
        Index(
            "ix_shadow_observations_opportunity",
            "opportunity_key",
            "strategy_version",
        ),
        Index(
            "ix_shadow_observations_evaluated",
            "evaluated_at",
            "instrument_id",
        ),
        CheckConstraint("stage = 'SHADOW'", name="shadow_stage_only"),
        CheckConstraint(
            "evidence_status IN ('EVALUATED','INPUT_UNAVAILABLE')",
            name="shadow_evidence_status_valid",
        ),
        CheckConstraint(
            "(evidence_status = 'EVALUATED' AND reason_code IS NULL) OR "
            "(evidence_status = 'INPUT_UNAVAILABLE' AND reason_code IS NOT NULL "
            "AND NOT signal_emitted)",
            name="shadow_evidence_reason_consistent",
        ),
        CheckConstraint(
            "(signal_emitted AND direction IS NOT NULL AND raw_edge_score IS NOT NULL "
            "AND entry_reference IS NOT NULL AND data_quality_state IS NOT NULL) OR "
            "(NOT signal_emitted AND direction IS NULL AND raw_edge_score IS NULL "
            "AND entry_reference IS NULL AND data_quality_state IS NULL)",
            name="shadow_signal_values_consistent",
        ),
    )


__all__ = ["ShadowObservation"]
