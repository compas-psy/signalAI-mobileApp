"""Verifier-only credentials for enrolled owner devices."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class DeviceCredential(Base):
    __tablename__ = "device_credentials"
    __table_args__ = (
        UniqueConstraint(
            "device_id",
            "generation",
            name="uq_device_credentials_generation",
        ),
        UniqueConstraint("token_verifier", name="uq_device_credentials_verifier"),
        UniqueConstraint("issued_request_hash", name="uq_device_credentials_request"),
        CheckConstraint(
            "generation > 0",
            name="generation_positive",
        ),
        CheckConstraint(
            "char_length(device_id) BETWEEN 16 AND 64",
            name="device_id_width",
        ),
        CheckConstraint(
            "char_length(token_verifier) = 64",
            name="verifier_width",
        ),
        CheckConstraint(
            "char_length(issued_request_hash) = 64",
            name="request_hash_width",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata_json) = 'object' "
            "AND octet_length(metadata_json::text) <= 256",
            name="metadata_bounded",
        ),
        Index("ix_device_credentials_active", "device_id", "revoked_at"),
        Index(
            "uq_device_credentials_one_active",
            "device_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    token_verifier: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_authenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DevicePairingSession(Base):
    """A verifier-only, owner-provisioned capability for bootstrap pairing.

    ``SIGNALAI_DEVICE_TOKEN`` deliberately is not represented here: it is a
    static bootstrap secret, while this row is the durable one-use (or small,
    bounded-use) authorization that prevents it from minting forever.
    """

    __tablename__ = "device_pairing_sessions"
    __table_args__ = (
        CheckConstraint(
            "char_length(session_verifier) = 64",
            name="verifier_width",
        ),
        CheckConstraint(
            "max_uses BETWEEN 1 AND 16",
            name="max_uses_bounded",
        ),
        CheckConstraint(
            "uses BETWEEN 0 AND max_uses",
            name="uses_bounded",
        ),
    )

    session_verifier: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False)
    uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DeviceOwnerKey(Base):
    """Public half of a biometric-per-use owner key held by Android Keystore."""

    __tablename__ = "device_owner_keys"
    __table_args__ = (
        CheckConstraint(
            "char_length(device_id) BETWEEN 16 AND 64",
            name="device_id_width",
        ),
        CheckConstraint(
            "algorithm = 'ECDSA_P256_SHA256'",
            name="algorithm_supported",
        ),
        CheckConstraint(
            "char_length(public_key_spki_b64) BETWEEN 80 AND 256",
            name="public_key_spki_bounded",
        ),
        CheckConstraint(
            "public_key_sha256 ~ '^[0-9a-f]{64}$'",
            name="public_key_sha256_hex",
        ),
        CheckConstraint(
            "enrolled_pairing_session_verifier ~ '^[0-9a-f]{64}$'",
            name="pairing_verifier_hex",
        ),
        UniqueConstraint(
            "public_key_sha256",
            name="uq_device_owner_keys_public_key_sha256",
        ),
        Index("ix_device_owner_keys_active", "device_id", "revoked_at"),
        Index(
            "uq_device_owner_keys_one_active",
            "device_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    public_key_spki_b64: Mapped[str] = mapped_column(String(256), nullable=False)
    public_key_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    enrolled_pairing_session_verifier: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OwnerStepUpChallenge(Base):
    """Single-use server challenge bound to one owner key and exact payload hash."""

    __tablename__ = "owner_step_up_challenges"
    __table_args__ = (
        CheckConstraint(
            "char_length(device_id) BETWEEN 16 AND 64",
            name="device_id_width",
        ),
        CheckConstraint(
            "purpose ~ '^[A-Z0-9_]{1,64}$'",
            name="purpose_bounded",
        ),
        CheckConstraint(
            "payload_hash ~ '^[0-9a-f]{64}$'",
            name="payload_hash_hex",
        ),
        CheckConstraint(
            "nonce_hex ~ '^[0-9a-f]{64}$'",
            name="nonce_hex_valid",
        ),
        CheckConstraint(
            "expires_at > issued_at",
            name="expiry_after_issue",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= issued_at",
            name="consumption_after_issue",
        ),
        UniqueConstraint("nonce_hex", name="uq_owner_step_up_challenges_nonce"),
        Index(
            "ix_owner_step_up_challenges_pending",
            "device_id",
            "expires_at",
            postgresql_where=text("consumed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_key_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("device_owner_keys.id", ondelete="RESTRICT"),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce_hex: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
