"""Durable owner controls for an already-open execution (SAI-050)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UuidPk, utcnow_column


class ExecutionManualTradeControl(UuidPk, Base):
    """Current durable state of one idempotent owner safety command.

    The command row is operational and may advance status. Forensic facts are
    separately appended to ``audit_events``. Raw idempotency keys are never
    persisted; only their SHA-256 digest is stored.
    """

    __tablename__ = "execution_manual_trade_controls"

    intent_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("execution_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    management_policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("execution_management_policy_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="REQUESTED", server_default=text("'REQUESTED'")
    )
    idempotency_key_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(32), nullable=False, default="owner")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_quantity: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    requested_stop: Mapped[Decimal | None] = mapped_column(Numeric(28, 12), nullable=True)
    reduce_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("execution_orders.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = utcnow_column()
    updated_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        UniqueConstraint(
            "intent_id",
            "idempotency_key_sha256",
            name="uq_execution_manual_trade_controls_intent_idempotency",
        ),
        CheckConstraint(
            "action IN ('CLOSE','REDUCE','TIGHTEN_STOP','RETURN_AUTO')",
            name="known_action",
        ),
        CheckConstraint("reduce_only IS TRUE", name="always_reduce_only"),
        CheckConstraint(
            "(action = 'CLOSE' AND requested_quantity IS NULL AND requested_stop IS NULL)"
            " OR (action = 'REDUCE' AND requested_quantity > 0 AND requested_stop IS NULL)"
            " OR (action = 'TIGHTEN_STOP' AND requested_quantity IS NULL AND requested_stop > 0)"
            " OR (action = 'RETURN_AUTO' AND requested_quantity IS NULL AND requested_stop IS NULL)",
            name="payload_matches_action",
        ),
        Index("ix_execution_manual_trade_controls_intent", "intent_id", "created_at"),
        Index("ix_execution_manual_trade_controls_status", "status", "created_at"),
    )


__all__ = ["ExecutionManualTradeControl"]
