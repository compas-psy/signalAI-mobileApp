"""Durable Lighter-specific execution identities for SAI-069.

These tables map SignalAI's provider-neutral order identity to Lighter's signed
64-bit client-order index and serialize explicit nonce ownership across worker
restarts. They do not send, sign or reconcile provider transactions.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UuidPk, utcnow_column


class LighterOrderIdentity(UuidPk, Base):
    __tablename__ = "lighter_order_identities"

    account_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(96), nullable=False)
    client_order_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint("account_index >= 0", name="account_index_non_negative"),
        CheckConstraint(
            "client_order_index > 0",
            name="client_order_index_positive",
        ),
        Index(
            "uq_lighter_order_identities_client_order_id",
            "client_order_id",
            unique=True,
        ),
        Index(
            "uq_lighter_order_identities_account_client_index",
            "account_index",
            "client_order_index",
            unique=True,
        ),
    )


class LighterNonceReservation(UuidPk, Base):
    __tablename__ = "lighter_nonce_reservations"

    account_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    api_key_index: Mapped[int] = mapped_column(Integer, nullable=False)
    replay_key: Mapped[str] = mapped_column(String(192), nullable=False)
    nonce: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="RESERVED", server_default=text("'RESERVED'")
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint("account_index >= 0", name="account_index_non_negative"),
        CheckConstraint(
            "api_key_index >= 0 AND api_key_index <= 253",
            name="api_key_index_range",
        ),
        CheckConstraint("nonce >= 0", name="nonce_non_negative"),
        CheckConstraint(
            "state IN ('RESERVED','CONSUMED')",
            name="state_valid",
        ),
        CheckConstraint(
            "(state = 'RESERVED' AND consumed_at IS NULL) OR "
            "(state = 'CONSUMED' AND consumed_at IS NOT NULL)",
            name="state_consumed_at_consistent",
        ),
        Index(
            "uq_lighter_nonce_reservations_replay_key",
            "replay_key",
            unique=True,
        ),
        Index(
            "uq_lighter_nonce_reservations_scope_nonce",
            "account_index",
            "api_key_index",
            "nonce",
            unique=True,
        ),
        Index(
            "uq_lighter_nonce_reservations_active_scope",
            "account_index",
            "api_key_index",
            unique=True,
            postgresql_where=text("state = 'RESERVED'"),
        ),
        Index(
            "ix_lighter_nonce_reservations_scope_state",
            "account_index",
            "api_key_index",
            "state",
        ),
    )


__all__ = ["LighterNonceReservation", "LighterOrderIdentity"]
