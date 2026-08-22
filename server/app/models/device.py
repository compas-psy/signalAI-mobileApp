"""Verifier-only credentials for enrolled owner devices."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
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
            name="ck_device_pairing_sessions_verifier_width",
        ),
        CheckConstraint(
            "max_uses BETWEEN 1 AND 16",
            name="ck_device_pairing_sessions_max_uses_bounded",
        ),
        CheckConstraint(
            "uses BETWEEN 0 AND max_uses",
            name="ck_device_pairing_sessions_uses_bounded",
        ),
    )

    session_verifier: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False)
    uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
