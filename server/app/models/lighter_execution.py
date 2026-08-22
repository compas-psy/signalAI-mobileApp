"""Durable Lighter-specific execution identities and evidence for R5.

These tables map SignalAI's provider-neutral order identity to Lighter's signed
64-bit client-order index, serialize explicit nonce ownership across worker
restarts, bind each provider action identity to one immutable request hash and
persist append-only provider/testnet evidence. They do not own the generic
ExecutionIntent lifecycle or enable LIVE execution.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
        CheckConstraint("client_order_index > 0", name="client_order_index_positive"),
        Index("uq_lighter_order_identities_client_order_id", "client_order_id", unique=True),
        Index(
            "uq_lighter_order_identities_account_client_index",
            "account_index",
            "client_order_index",
            unique=True,
        ),
    )


class LighterOrderActionBinding(UuidPk, Base):
    __tablename__ = "lighter_order_action_bindings"

    action_key: Mapped[str] = mapped_column(String(192), nullable=False)
    action_type: Mapped[str] = mapped_column(String(16), nullable=False)
    account_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    api_key_index: Mapped[int] = mapped_column(Integer, nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(96), nullable=False)
    client_order_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    market_index: Mapped[int] = mapped_column(Integer, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint(
            "action_type IN ('CREATE','CANCEL','REDUCE','PROTECT')",
            name="action_type_valid",
        ),
        CheckConstraint("account_index >= 0", name="account_index_non_negative"),
        CheckConstraint(
            "api_key_index >= 0 AND api_key_index <= 253",
            name="api_key_index_range",
        ),
        CheckConstraint("client_order_index > 0", name="client_order_index_positive"),
        CheckConstraint("market_index >= 0", name="market_index_non_negative"),
        CheckConstraint(
            "char_length(request_hash) = 64",
            name="request_hash_sha256_width",
        ),
        Index(
            "uq_lighter_order_action_bindings_action_key",
            "action_key",
            unique=True,
        ),
        Index(
            "ix_lighter_order_action_bindings_scope",
            "account_index",
            "api_key_index",
            "created_at",
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
            "state IN ('RESERVED','SUBMITTING','CONSUMED')",
            name="state_valid",
        ),
        CheckConstraint(
            "(state IN ('RESERVED','SUBMITTING') AND consumed_at IS NULL) OR "
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
            postgresql_where=text("state IN ('RESERVED','SUBMITTING')"),
        ),
        Index(
            "ix_lighter_nonce_reservations_scope_state",
            "account_index",
            "api_key_index",
            "state",
        ),
    )


class LighterReconciliationEvidence(UuidPk, Base):
    __tablename__ = "lighter_reconciliation_evidence"

    evidence_key: Mapped[str] = mapped_column(String(64), nullable=False)
    action_key: Mapped[str] = mapped_column(String(192), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    account_index: Mapped[int] = mapped_column(BigInteger, nullable=False)
    api_key_index: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_nonce: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider_next_nonce: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_order_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_tx_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('ORDER_FOUND','TX_FOUND','AMBIGUOUS','CONSUMED_UNKNOWN')",
            name="outcome_valid",
        ),
        CheckConstraint("account_index >= 0", name="account_index_non_negative"),
        CheckConstraint(
            "api_key_index >= 0 AND api_key_index <= 253",
            name="api_key_index_range",
        ),
        CheckConstraint("reserved_nonce >= 0", name="reserved_nonce_non_negative"),
        CheckConstraint(
            "provider_next_nonce >= reserved_nonce",
            name="provider_nonce_floor",
        ),
        CheckConstraint(
            "char_length(evidence_key) = 64",
            name="evidence_key_sha256_width",
        ),
        Index(
            "uq_lighter_reconciliation_evidence_key",
            "evidence_key",
            unique=True,
        ),
        Index(
            "ix_lighter_reconciliation_evidence_action",
            "action_key",
            "observed_at",
        ),
    )


class LighterTestnetSmokeEvidence(UuidPk, Base):
    """Append-only, redacted evidence from one owner-authorized testnet smoke."""

    __tablename__ = "lighter_testnet_smoke_evidence"

    evidence_key: Mapped[str] = mapped_column(String(64), nullable=False)
    run_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scorecard_status: Mapped[str] = mapped_column(String(32), nullable=False)
    scorecard_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    scorecard_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    account_index: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    api_key_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    market_index: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    client_order_id: Mapped[str] = mapped_column(String(96), nullable=False)
    create_tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cancel_tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    eligible_for_live: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint(
            "char_length(evidence_key) = 64",
            name="evidence_key_sha256_width",
        ),
        CheckConstraint("char_length(run_key) = 64", name="run_key_sha256_width"),
        CheckConstraint(
            "char_length(source_sha256) = 64",
            name="source_sha256_width",
        ),
        CheckConstraint(
            "event_type IN ('BLOCKED','SUCCESS','CREATE_FAILED','CANCEL_FAILED',"
            "'RECOVERY_SUCCESS')",
            name="event_type_valid",
        ),
        CheckConstraint(
            "account_index IS NULL OR account_index >= 0",
            name="account_index_non_negative",
        ),
        CheckConstraint(
            "api_key_index IS NULL OR (api_key_index >= 0 AND api_key_index <= 253)",
            name="api_key_index_range",
        ),
        CheckConstraint("market_index >= 0", name="market_index_non_negative"),
        CheckConstraint("eligible_for_live = false", name="live_always_false"),
        Index(
            "uq_lighter_testnet_smoke_evidence_key",
            "evidence_key",
            unique=True,
        ),
        Index(
            "ix_lighter_testnet_smoke_evidence_run",
            "run_key",
            "observed_at",
        ),
    )


__all__ = [
    "LighterNonceReservation",
    "LighterOrderActionBinding",
    "LighterOrderIdentity",
    "LighterReconciliationEvidence",
    "LighterTestnetSmokeEvidence",
]
