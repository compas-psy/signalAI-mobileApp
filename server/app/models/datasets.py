"""Immutable manifests for reproducible feature datasets.

Dataset snapshots are measurement/data lineage state. They do not decide which
trading strategy runs and are never a mutable runtime toggle.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamp, UuidPk, utcnow_column


class DatasetSnapshot(UuidPk, Base):
    """Append-only identity and audit metadata for one content-addressed dataset."""

    __tablename__ = "dataset_snapshots"

    dataset_name: Mapped[str] = mapped_column(String(96), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)

    tradable_at: Mapped[Timestamp] = mapped_column(nullable=False)
    source_watermark: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)

    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        UniqueConstraint(
            "dataset_name",
            "snapshot_id",
            name="uq_dataset_snapshots_dataset_name_snapshot_id",
        ),
        Index(
            "ix_dataset_snapshots_resolve",
            "dataset_name",
            "tradable_at",
            "created_at",
        ),
        CheckConstraint("row_count >= 0", name="row_count_non_negative"),
    )


__all__ = ["DatasetSnapshot"]
