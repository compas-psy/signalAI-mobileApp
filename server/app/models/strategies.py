"""Versioned strategy registry and append-only governance history.

These tables are measurement/governance state. They do not decide whether the
production scanner runs or whether an already-issued idea advances through its
paper lifecycle.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UuidPk, utcnow_column


class StrategyVersion(UuidPk, Base):
    """Immutable identity of one `(family, version)` strategy implementation."""

    __tablename__ = "strategy_versions"

    family: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    venue_allowlist: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)), nullable=False, default=list
    )
    instrument_prefixes: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list
    )
    created_at: Mapped[datetime] = utcnow_column()

    events: Mapped[list["StrategyPromotionEvent"]] = relationship(
        back_populates="strategy_version",
        order_by="StrategyPromotionEvent.sequence",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("family", "version", name="uq_strategy_versions_family_version"),
        Index("ix_strategy_versions_family", "family"),
    )


class StrategyPromotionEvent(Base):
    """Append-only snapshot of a strategy governance decision.

    Role, enabled stages and UI visibility are reconstructed from the latest
    event. There is intentionally no mutable `current_role` column that an
    environment flag or ad-hoc UPDATE could silently change.
    """

    __tablename__ = "strategy_promotion_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "strategy_versions.id",
            name="fk_strategy_promotion_events_version",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = utcnow_column()

    actor: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_role: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled_stages: Mapped[list[str]] = mapped_column(
        ARRAY(String(24)), nullable=False
    )
    ui_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    decision_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    detail_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    strategy_version: Mapped[StrategyVersion] = relationship(back_populates="events")

    __table_args__ = (
        UniqueConstraint(
            "strategy_version_id",
            "sequence",
            name="uq_strategy_promotion_events_sequence",
        ),
        Index("ix_strategy_promotion_events_time", "occurred_at"),
    )
