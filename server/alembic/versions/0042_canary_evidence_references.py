"""Persist append-only trusted Canary evidence reference metadata.

revision = 0042_canary_evidence_references
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0042_canary_evidence_references"
down_revision = "0041_lighter_canary_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "canary_evidence_references",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("evidence_ref", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("source_sha", sa.String(length=40), nullable=False),
        sa.Column("engine_config_hash", sa.String(length=64), nullable=False),
        sa.Column("strategy_family", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("venue", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fresh_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category IN ('strategy_performance','shadow','testnet',"
            "'protection_reconciliation','kill_switch_drill','security_scan',"
            "'operational_health')",
            name=op.f("ck_canary_evidence_references_category_valid"),
        ),
        sa.CheckConstraint(
            "verdict IN ('VERIFIED','FAILED')",
            name=op.f("ck_canary_evidence_references_verdict_valid"),
        ),
        sa.CheckConstraint(
            "char_length(evidence_ref) BETWEEN 1 AND 128",
            name=op.f("ck_canary_evidence_references_ref_non_empty"),
        ),
        sa.CheckConstraint(
            "char_length(source) BETWEEN 1 AND 64",
            name=op.f("ck_canary_evidence_references_source_non_empty"),
        ),
        sa.CheckConstraint(
            "char_length(artifact_sha256) = 64",
            name=op.f("ck_canary_evidence_references_artifact_sha256_width"),
        ),
        sa.CheckConstraint(
            "char_length(source_sha) = 40",
            name=op.f("ck_canary_evidence_references_source_sha_width"),
        ),
        sa.CheckConstraint(
            "char_length(engine_config_hash) = 64",
            name=op.f("ck_canary_evidence_references_config_hash_width"),
        ),
        sa.CheckConstraint(
            "fresh_until >= observed_at",
            name=op.f("ck_canary_evidence_references_freshness_ordered"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canary_evidence_references")),
        sa.UniqueConstraint(
            "evidence_ref",
            name="uq_canary_evidence_references_evidence_ref",
        ),
    )
    op.create_index(
        "ix_canary_evidence_references_scope_category",
        "canary_evidence_references",
        [
            "source_sha",
            "engine_config_hash",
            "strategy_family",
            "strategy_version",
            "venue",
            "category",
        ],
    )
    op.execute(
        "CREATE TRIGGER trg_canary_evidence_references_append_only "
        "BEFORE UPDATE OR DELETE ON canary_evidence_references "
        "FOR EACH ROW EXECUTE FUNCTION signalai_append_only()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_canary_evidence_references_append_only "
        "ON canary_evidence_references"
    )
    op.drop_index(
        "ix_canary_evidence_references_scope_category",
        table_name="canary_evidence_references",
    )
    op.drop_table("canary_evidence_references")
