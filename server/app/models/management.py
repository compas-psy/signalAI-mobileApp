"""Immutable open-trade management policy snapshots (SAI-049)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UuidPk, utcnow_column


class ExecutionManagementPolicySnapshot(UuidPk, Base):
    """Frozen policy facts that govern one already-protected position.

    An optimizer/config release may change future trades, but this row is
    append-only and keyed one-to-one to the execution intent so an opened
    position keeps the exact strategy/risk/exit/venue contract it started with.
    """

    __tablename__ = "execution_management_policy_snapshots"

    intent_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("execution_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("risk_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    risk_override_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("execution_risk_overrides.id", ondelete="RESTRICT"),
        nullable=True,
    )
    risk_policy_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    manual_override_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    exit_profile_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    venue_rules_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        UniqueConstraint(
            "intent_id",
            name="uq_execution_management_policy_snapshots_intent",
        ),
        Index(
            "ix_execution_management_policy_snapshots_created",
            "created_at",
        ),
    )


__all__ = ["ExecutionManagementPolicySnapshot"]
