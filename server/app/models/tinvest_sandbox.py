"""Provider-confirmed T-Invest Sandbox round-trip acceptance evidence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UuidPk, utcnow_column


class TInvestSandboxRoundTripProof(UuidPk, Base):
    """Append-only proof bound to one exact release and credential generation."""

    __tablename__ = "tinvest_sandbox_roundtrip_proofs"

    source_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    credential_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    account_suffix: Mapped[str] = mapped_column(String(32), nullable=False)
    buy_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    buy_status: Mapped[str] = mapped_column(String(64), nullable=False)
    buy_executed_lots: Mapped[int] = mapped_column(Integer, nullable=False)
    sell_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sell_status: Mapped[str] = mapped_column(String(64), nullable=False)
    sell_executed_lots: Mapped[int] = mapped_column(Integer, nullable=False)
    position_flat: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    observed_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint(
            "source_sha ~ '^[0-9a-f]{40}$'",
            name="source_sha_git_width",
        ),
        CheckConstraint("buy_executed_lots > 0", name="buy_lots_positive"),
        CheckConstraint(
            "sell_executed_lots = buy_executed_lots",
            name="sell_matches_buy_lots",
        ),
        CheckConstraint("position_flat = true", name="position_must_be_flat"),
        Index(
            "uq_tinvest_sandbox_roundtrip_release_credential",
            "source_sha",
            "credential_updated_at",
            unique=True,
        ),
    )


__all__ = ["TInvestSandboxRoundTripProof"]
