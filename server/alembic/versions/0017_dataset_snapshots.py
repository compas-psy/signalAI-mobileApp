"""Persist immutable content-addressed dataset snapshot manifests.

revision = 0017_dataset_snapshots
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_dataset_snapshots"
down_revision = "0016_strategy_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("dataset_name", sa.String(length=96), nullable=False),
        sa.Column("dataset_version", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("tradable_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source_watermark",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_key", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "row_count >= 0",
            name="ck_dataset_snapshots_row_count_non_negative",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dataset_snapshots"),
        sa.UniqueConstraint(
            "dataset_name",
            "snapshot_id",
            name="uq_dataset_snapshots_dataset_name_snapshot_id",
        ),
    )
    op.create_index(
        "ix_dataset_snapshots_resolve",
        "dataset_snapshots",
        ["dataset_name", "tradable_at", "created_at"],
        unique=False,
    )
    op.execute(
        """
        CREATE TRIGGER dataset_snapshots_append_only
        BEFORE UPDATE OR DELETE ON dataset_snapshots
        FOR EACH ROW EXECUTE FUNCTION signalai_append_only();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS dataset_snapshots_append_only ON dataset_snapshots;"
    )
    op.drop_index("ix_dataset_snapshots_resolve", table_name="dataset_snapshots")
    op.drop_table("dataset_snapshots")
