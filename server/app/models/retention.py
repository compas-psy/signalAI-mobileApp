"""Immutable evidence for a destructive retention attempt."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, utcnow_column


class RetentionAttemptIntent(Base):
    """Committed authorization record written before a retention unlink."""

    __tablename__ = "retention_attempt_intents"

    attempt_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_budget_files: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_budget_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    root_hashes_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint("owner_budget_files > 0", name="owner_budget_files_positive"),
        CheckConstraint("owner_budget_bytes > 0", name="owner_budget_bytes_positive"),
        CheckConstraint("char_length(config_hash) = 64", name="config_hash_sha256_width"),
    )


class RetentionAttemptOutcome(Base):
    """One immutable result correlated to a previously committed intent."""

    __tablename__ = "retention_attempt_outcomes"

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("retention_attempt_intents.attempt_id"),
        primary_key=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = utcnow_column()


__all__ = ["RetentionAttemptIntent", "RetentionAttemptOutcome"]
