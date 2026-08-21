"""Persist append-only promotion evidence and decisions.

revision = 0038_promotion_evidence
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0038_promotion_evidence"
down_revision = "0037_retention_attempt_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "promotion_evidence_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("evidence_version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("strategy_family", sa.String(length=32), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("venue", sa.String(length=32), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fresh_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("gate_passed", sa.Boolean(), nullable=False),
        sa.Column("reconciliation_verified", sa.Boolean(), nullable=False),
        sa.Column("protection_verified", sa.Boolean(), nullable=False),
        sa.Column("kill_switch_verified", sa.Boolean(), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('TECHNICAL', 'PERFORMANCE', 'OPERATIONS')",
            name=op.f("ck_promotion_evidence_snapshots_kind_valid"),
        ),
        sa.CheckConstraint(
            "evidence_version > 0",
            name=op.f("ck_promotion_evidence_snapshots_version_positive"),
        ),
        sa.CheckConstraint(
            "sample_size >= 0",
            name=op.f("ck_promotion_evidence_snapshots_sample_non_negative"),
        ),
        sa.CheckConstraint(
            "error_count >= 0 AND error_count <= sample_size",
            name=op.f("ck_promotion_evidence_snapshots_error_in_sample"),
        ),
        sa.CheckConstraint(
            "fresh_until >= observed_at",
            name=op.f("ck_promotion_evidence_snapshots_freshness_ordered"),
        ),
        sa.CheckConstraint(
            "char_length(source_hash) = 64",
            name=op.f(
                "ck_promotion_evidence_snapshots_source_hash_sha256_width"
            ),
        ),
        sa.CheckConstraint(
            "char_length(config_hash) = 64",
            name=op.f(
                "ck_promotion_evidence_snapshots_config_hash_sha256_width"
            ),
        ),
        sa.CheckConstraint(
            "char_length(policy_hash) = 64",
            name=op.f(
                "ck_promotion_evidence_snapshots_policy_hash_sha256_width"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotion_evidence_snapshots")),
    )
    op.create_index(
        "ix_promotion_evidence_snapshots_scope",
        "promotion_evidence_snapshots",
        ["strategy_family", "strategy_version", "venue", "kind", "observed_at"],
    )
    op.create_table(
        "promotion_evidence_decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("current_mode", sa.String(length=12), nullable=False),
        sa.Column("target_mode", sa.String(length=12), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column(
            "blockers_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "evidence_snapshot_ids_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("strategy_family", sa.String(length=32), nullable=True),
        sa.Column("strategy_version", sa.String(length=64), nullable=True),
        sa.Column("venue", sa.String(length=32), nullable=True),
        sa.Column("source_hash", sa.String(length=64), nullable=True),
        sa.Column("config_hash", sa.String(length=64), nullable=True),
        sa.Column("policy_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(correlation_id) > 0",
            name=op.f("ck_promotion_evidence_decisions_correlation_non_empty"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotion_evidence_decisions")),
    )
    op.create_index(
        "uq_promotion_evidence_decisions_correlation",
        "promotion_evidence_decisions",
        ["correlation_id"],
        unique=True,
    )
    op.create_index(
        "ix_promotion_evidence_decisions_occurred",
        "promotion_evidence_decisions",
        ["occurred_at"],
    )
    for table, trigger in (
        ("promotion_evidence_snapshots", "trg_promotion_evidence_snapshots_append_only"),
        ("promotion_evidence_decisions", "trg_promotion_evidence_decisions_append_only"),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION signalai_append_only()"
        )


def downgrade() -> None:
    for table, trigger in (
        ("promotion_evidence_decisions", "trg_promotion_evidence_decisions_append_only"),
        ("promotion_evidence_snapshots", "trg_promotion_evidence_snapshots_append_only"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    op.drop_index(
        "ix_promotion_evidence_decisions_occurred",
        table_name="promotion_evidence_decisions",
    )
    op.drop_index(
        "uq_promotion_evidence_decisions_correlation",
        table_name="promotion_evidence_decisions",
    )
    op.drop_table("promotion_evidence_decisions")
    op.drop_index(
        "ix_promotion_evidence_snapshots_scope",
        table_name="promotion_evidence_snapshots",
    )
    op.drop_table("promotion_evidence_snapshots")
