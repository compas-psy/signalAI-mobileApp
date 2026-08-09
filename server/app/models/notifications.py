"""Durable one-device notification outbox."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class NotificationOutbox(Base):
    __tablename__ = "signalai_notification_outbox"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    dedup_key: Mapped[str] = mapped_column(String(240), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("''"),
    )


__all__ = ["NotificationOutbox"]
