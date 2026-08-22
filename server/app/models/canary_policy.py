"""Append-only Lighter live credential generations and Canary policy snapshots.

These tables deliberately store *non-secret* authorization facts only.  Raw
provider keys, key-derived fingerprints and provider responses must never enter
this schema.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UuidPk, utcnow_column


class LighterCredentialGeneration(Base):
    """Opaque append-only generation fact for the ``lighter_trade`` slot."""

    __tablename__ = "lighter_credential_generations"
    __table_args__ = (
        UniqueConstraint("generation_id", name="uq_lighter_credential_generations_generation_id"),
        CheckConstraint("slot = 'lighter_trade'", name="slot_live_trade_only"),
        CheckConstraint(
            "action IN ('CREATED', 'ROTATED', 'REVOKED')",
            name="action_valid",
        ),
        CheckConstraint("char_length(actor) BETWEEN 1 AND 64", name="actor_non_empty"),
        CheckConstraint("account_index >= 0", name="account_index_non_negative"),
        CheckConstraint("api_key_index BETWEEN 0 AND 253", name="api_key_index_bounded"),
        Index("ix_lighter_credential_generations_slot_id", "slot", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    generation_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    slot: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    account_index: Mapped[int] = mapped_column(Integer, nullable=False)
    api_key_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CanaryPolicySnapshot(UuidPk, Base):
    """Immutable canonical non-secret policy input for a future Canary run."""

    __tablename__ = "canary_policy_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_hash", name="uq_canary_policy_snapshots_snapshot_hash"),
        CheckConstraint("schema_version > 0", name="schema_version_positive"),
        CheckConstraint("char_length(snapshot_hash) = 64", name="snapshot_hash_sha256_width"),
        CheckConstraint("char_length(source_sha) = 40", name="source_sha_width"),
        CheckConstraint("char_length(engine_config_hash) = 64", name="config_hash_sha256_width"),
        CheckConstraint("account_index >= 0", name="account_index_non_negative"),
        CheckConstraint("api_key_index BETWEEN 0 AND 253", name="api_key_index_bounded"),
        CheckConstraint("char_length(actor) BETWEEN 1 AND 64", name="actor_non_empty"),
        CheckConstraint(
            "char_length(correlation_id) BETWEEN 1 AND 128",
            name="correlation_non_empty",
        ),
        CheckConstraint("jsonb_typeof(payload_json) = 'object'", name="payload_object"),
        Index(
            "ix_canary_policy_snapshots_generation_created",
            "credential_generation_id",
            "created_at",
        ),
    )

    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    engine_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_generation_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("lighter_credential_generations.generation_id"),
        nullable=False,
    )
    account_index: Mapped[int] = mapped_column(Integer, nullable=False)
    api_key_index: Mapped[int] = mapped_column(Integer, nullable=False)
    strategy_family: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = utcnow_column()


__all__ = ["CanaryPolicySnapshot", "LighterCredentialGeneration"]
