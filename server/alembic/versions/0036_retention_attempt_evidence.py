"""Add immutable intent/outcome evidence for retention deletion.

revision = 0037_retention_attempt_evidence
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0037_retention_attempt_evidence"
down_revision = "0036_rebalance_economics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retention_attempt_intents",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("owner_budget_files", sa.BigInteger(), nullable=False),
        sa.Column("owner_budget_bytes", sa.BigInteger(), nullable=False),
        sa.Column("root_hashes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("owner_budget_files > 0", name=op.f("ck_retention_attempt_intents_owner_budget_files_positive")),
        sa.CheckConstraint("owner_budget_bytes > 0", name=op.f("ck_retention_attempt_intents_owner_budget_bytes_positive")),
        sa.CheckConstraint("char_length(config_hash) = 64", name=op.f("ck_retention_attempt_intents_config_hash_sha256_width")),
        sa.PrimaryKeyConstraint("attempt_id", name=op.f("pk_retention_attempt_intents")),
    )
    op.create_table(
        "retention_attempt_outcomes",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["retention_attempt_intents.attempt_id"], name=op.f("fk_retention_attempt_outcomes_attempt_id_retention_attempt_intents")),
        sa.PrimaryKeyConstraint("attempt_id", name=op.f("pk_retention_attempt_outcomes")),
    )
    for table in ("retention_attempt_intents", "retention_attempt_outcomes"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION signalai_append_only()"
        )


def downgrade() -> None:
    for table in ("retention_attempt_outcomes", "retention_attempt_intents"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
    op.drop_table("retention_attempt_outcomes")
    op.drop_table("retention_attempt_intents")
