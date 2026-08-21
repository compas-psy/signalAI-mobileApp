"""Append-only counterfactual Paper A/B measurement facts.

These tables are intentionally disconnected from owner ``TradeIdea`` /
``PaperTrade`` / execution lifecycles.  A decision is immutable once observed;
an outcome is a second immutable fact.  Absence of an outcome row means the
measurement horizon is still pending and must never be rewritten to zero PnL.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UuidPk, utcnow_column


class PaperAbDecision(UuidPk, Base):
    """One arm's immutable Paper A/B decision on a candidate-specific pair."""

    __tablename__ = "paper_ab_decisions"

    decision_key: Mapped[str] = mapped_column(String(64), nullable=False)
    pair_key: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_version: Mapped[str] = mapped_column(String(64), nullable=False)
    arm_role: Mapped[str] = mapped_column(String(16), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(64), nullable=False)
    venue: Mapped[str] = mapped_column(String(32), nullable=False)
    regime: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    market_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cost_model_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    signal_emitted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    entry_reference: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(12, 8), nullable=True)

    # Both arms of one candidate-specific pair share the same measurement
    # horizon/risk unit.  Cost/risk values may be absent; that absence is
    # preserved and later yields INPUT_UNAVAILABLE rather than invented PnL.
    horizon_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    round_trip_cost_bps: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        UniqueConstraint("decision_key", name="uq_paper_ab_decisions_key"),
        UniqueConstraint("pair_key", "arm_role", name="uq_paper_ab_decisions_pair_arm"),
        Index("ix_paper_ab_decisions_candidate_time", "candidate_version", "decision_at"),
        Index("ix_paper_ab_decisions_pair", "pair_key"),
        CheckConstraint(
            "arm_role IN ('CONTROL','CANDIDATE')",
            name="paper_ab_arm_role_valid",
        ),
        CheckConstraint("horizon_minutes > 0", name="paper_ab_horizon_positive"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="paper_ab_confidence_probability",
        ),
        CheckConstraint(
            "risk_unit_price IS NULL OR risk_unit_price > 0",
            name="paper_ab_risk_unit_positive",
        ),
        CheckConstraint(
            "round_trip_cost_bps IS NULL OR round_trip_cost_bps >= 0",
            name="paper_ab_cost_non_negative",
        ),
        CheckConstraint(
            "(signal_emitted AND direction IN ('LONG','SHORT') AND entry_reference IS NOT NULL) "
            "OR (NOT signal_emitted AND direction IS NULL AND entry_reference IS NULL)",
            name="paper_ab_signal_values_consistent",
        ),
    )


class PaperAbOutcome(Base):
    """Immutable terminal evidence for one Paper A/B decision.

    There is deliberately no ``PENDING`` row.  Pending means *no outcome fact
    exists yet*, so a delayed data feed can resolve the decision later without
    mutating history.
    """

    __tablename__ = "paper_ab_outcomes"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("paper_ab_decisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    evidence_status: Mapped[str] = mapped_column(String(24), nullable=False)
    net_r: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    exit_reference: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    outcome_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        UniqueConstraint("decision_id", name="uq_paper_ab_outcomes_decision"),
        Index("ix_paper_ab_outcomes_time", "outcome_at"),
        CheckConstraint(
            "evidence_status IN ('EVALUATED','INPUT_UNAVAILABLE')",
            name="paper_ab_outcome_status_valid",
        ),
        CheckConstraint(
            "(evidence_status = 'EVALUATED' AND reason_code IS NULL) OR "
            "(evidence_status = 'INPUT_UNAVAILABLE' AND reason_code IS NOT NULL "
            "AND net_r IS NULL AND exit_reference IS NULL)",
            name="paper_ab_outcome_reason_consistent",
        ),
    )


__all__ = ["PaperAbDecision", "PaperAbOutcome"]
