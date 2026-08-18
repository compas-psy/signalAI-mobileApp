"""Persistent domain for the server-side execution core (SAI-024 / B5.1).

These models are deliberately not wired into owner approval, workers, venue
adapters or the current paper lifecycle yet. Later slices may create and
advance records only through their explicit risk/mode/reconciliation gates.

Mutable rows represent current operational state (intent/order/protection and
the singleton mode state). Facts that must survive forensic review are stored
as append-only mode events, fills and reconciliation events at the database
layer by migration 0020.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from ..execution.enums import ExecutionLifecycleMode, ExecutionState
from .base import Base, Money, Price, Quantity, StrEnumColumn, UuidPk, utcnow_column


class ExecutionModeState(Base):
    """Reserved singleton for the server-owned mode implemented in SAI-030.

    Until SAI-030 wires this table into runtime, the existing risk_state path
    remains authoritative. The persisted default is the least-risk PAPER mode.
    """

    __tablename__ = "execution_mode_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    mode: Mapped[ExecutionLifecycleMode] = mapped_column(
        StrEnumColumn(ExecutionLifecycleMode, 12),
        nullable=False,
        default=ExecutionLifecycleMode.PAPER,
    )
    updated_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)


class ExecutionModeEvent(UuidPk, Base):
    __tablename__ = "execution_mode_events"

    from_mode: Mapped[ExecutionLifecycleMode | None] = mapped_column(
        StrEnumColumn(ExecutionLifecycleMode, 12), nullable=True
    )
    to_mode: Mapped[ExecutionLifecycleMode] = mapped_column(
        StrEnumColumn(ExecutionLifecycleMode, 12), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    detail_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (Index("ix_execution_mode_events_occurred", "occurred_at"),)


class ExecutionIntent(UuidPk, Base):
    __tablename__ = "execution_intents"

    # Content-addressed stable identity from B5.2. The hash deliberately covers
    # decision identity (idea/strategy/risk/venue/account), not mutable delivery
    # state. PostgreSQL uniqueness is the final retry/concurrency guard.
    identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idea_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("trade_ideas.id", ondelete="RESTRICT"),
        nullable=False,
    )
    instrument_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("instruments.instrument_id", ondelete="RESTRICT"),
        nullable=False,
    )
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_policy_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("risk_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # SAI-042 owns the risk-override table. Preserve stable identity now without
    # inventing a foreign key to a table that intentionally does not exist yet.
    risk_override_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    venue: Mapped[str] = mapped_column(String(32), nullable=False)
    account: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[ExecutionState] = mapped_column(
        StrEnumColumn(ExecutionState, 24),
        nullable=False,
        default=ExecutionState.INTENT_CREATED,
    )

    planned_quantity: Mapped[Quantity] = mapped_column(nullable=False)
    planned_entry_price: Mapped[Price] = mapped_column(nullable=False)
    planned_stop_price: Mapped[Price] = mapped_column(nullable=False)

    # SAI-027 durable delivery metadata. These fields are operational state,
    # not strategy/risk inputs: they only control when and by whom the same
    # content-addressed execution intent may be replayed after uncertainty.
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = utcnow_column()
    updated_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        UniqueConstraint("identity_hash", name="uq_execution_intents_identity_hash"),
        Index("ix_execution_intents_state", "state"),
        Index("ix_execution_intents_idea", "idea_id"),
        Index("ix_execution_intents_retry_due", "state", "next_retry_at"),
        Index("ix_execution_intents_lease_expiry", "lease_expires_at"),
        Index(
            "ix_execution_intents_venue_account_instrument",
            "venue",
            "account",
            "instrument_id",
        ),
    )


class ExecutionOrder(UuidPk, Base):
    __tablename__ = "execution_orders"

    intent_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("execution_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    client_order_id: Mapped[str] = mapped_column(String(96), nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    order_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    quantity: Mapped[Quantity] = mapped_column(nullable=False)
    limit_price: Mapped[Price | None] = mapped_column(nullable=True)
    stop_price: Mapped[Price | None] = mapped_column(nullable=True)

    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = utcnow_column()
    updated_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        UniqueConstraint("client_order_id", name="uq_execution_orders_client_order_id"),
        Index("ix_execution_orders_intent", "intent_id"),
        Index("ix_execution_orders_provider_order", "provider_order_id"),
    )


class ExecutionFill(UuidPk, Base):
    __tablename__ = "execution_fills"

    intent_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("execution_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("execution_orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider_fill_id: Mapped[str] = mapped_column(String(128), nullable=False)
    quantity: Mapped[Quantity] = mapped_column(nullable=False)
    price: Mapped[Price] = mapped_column(nullable=False)
    fee_amount: Mapped[Money] = mapped_column(nullable=False, default=Decimal("0"))
    fee_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        UniqueConstraint("order_id", "provider_fill_id", name="uq_execution_fills_provider"),
        CheckConstraint("quantity > 0", name="positive_quantity"),
        Index("ix_execution_fills_intent", "intent_id"),
        Index("ix_execution_fills_filled", "filled_at"),
    )


class ExecutionProtection(UuidPk, Base):
    __tablename__ = "execution_protections"

    intent_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("execution_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("execution_orders.id", ondelete="RESTRICT"),
        nullable=True,
    )
    protection_type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    quantity: Mapped[Quantity] = mapped_column(nullable=False)
    stop_price: Mapped[Price] = mapped_column(nullable=False)
    armed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = utcnow_column()
    updated_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_quantity"),
        Index("ix_execution_protections_intent", "intent_id"),
    )


class ExecutionReconciliationEvent(UuidPk, Base):
    __tablename__ = "execution_reconciliation_events"

    intent_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("execution_intents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    detail_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        Index("ix_execution_reconciliation_intent", "intent_id"),
        Index("ix_execution_reconciliation_occurred", "occurred_at"),
    )


__all__ = [
    "ExecutionFill",
    "ExecutionIntent",
    "ExecutionModeEvent",
    "ExecutionModeState",
    "ExecutionOrder",
    "ExecutionProtection",
    "ExecutionReconciliationEvent",
]
