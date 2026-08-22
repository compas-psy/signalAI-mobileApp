"""Persist append-only Lighter testnet smoke evidence.

revision = 0036_lighter_testnet_smoke
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036_lighter_testnet_smoke"
down_revision = "0035_lighter_reconciliation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lighter_testnet_smoke_evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("evidence_key", sa.String(length=64), nullable=False),
        sa.Column("run_key", sa.String(length=64), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("scorecard_status", sa.String(length=32), nullable=False),
        sa.Column("scorecard_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scorecard_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_index", sa.BigInteger(), nullable=True),
        sa.Column("api_key_index", sa.Integer(), nullable=True),
        sa.Column("market_index", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("client_order_id", sa.String(length=96), nullable=False),
        sa.Column("create_tx_hash", sa.String(length=128), nullable=True),
        sa.Column("cancel_tx_hash", sa.String(length=128), nullable=True),
        sa.Column(
            "eligible_for_live",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(evidence_key) = 64",
            name=op.f("ck_lighter_testnet_smoke_evidence_evidence_key_sha256_width"),
        ),
        sa.CheckConstraint(
            "char_length(run_key) = 64",
            name=op.f("ck_lighter_testnet_smoke_evidence_run_key_sha256_width"),
        ),
        sa.CheckConstraint(
            "char_length(source_sha256) = 64",
            name=op.f("ck_lighter_testnet_smoke_evidence_source_sha256_width"),
        ),
        sa.CheckConstraint(
            "event_type IN ('BLOCKED','SUCCESS','CREATE_FAILED','CANCEL_FAILED',"
            "'RECOVERY_SUCCESS')",
            name=op.f("ck_lighter_testnet_smoke_evidence_event_type_valid"),
        ),
        sa.CheckConstraint(
            "account_index IS NULL OR account_index >= 0",
            name=op.f("ck_lighter_testnet_smoke_evidence_account_index_non_negative"),
        ),
        sa.CheckConstraint(
            "api_key_index IS NULL OR (api_key_index >= 0 AND api_key_index <= 253)",
            name=op.f("ck_lighter_testnet_smoke_evidence_api_key_index_range"),
        ),
        sa.CheckConstraint(
            "market_index >= 0",
            name=op.f("ck_lighter_testnet_smoke_evidence_market_index_non_negative"),
        ),
        sa.CheckConstraint(
            "eligible_for_live = false",
            name=op.f("ck_lighter_testnet_smoke_evidence_live_always_false"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lighter_testnet_smoke_evidence")),
    )
    op.create_index(
        "uq_lighter_testnet_smoke_evidence_key",
        "lighter_testnet_smoke_evidence",
        ["evidence_key"],
        unique=True,
    )
    op.create_index(
        "ix_lighter_testnet_smoke_evidence_run",
        "lighter_testnet_smoke_evidence",
        ["run_key", "observed_at"],
        unique=False,
    )
    op.execute(
        "CREATE TRIGGER trg_lighter_testnet_smoke_evidence_append_only "
        "BEFORE UPDATE OR DELETE ON lighter_testnet_smoke_evidence "
        "FOR EACH ROW EXECUTE FUNCTION signalai_append_only()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_lighter_testnet_smoke_evidence_append_only "
        "ON lighter_testnet_smoke_evidence"
    )
    op.drop_index(
        "ix_lighter_testnet_smoke_evidence_run",
        table_name="lighter_testnet_smoke_evidence",
    )
    op.drop_index(
        "uq_lighter_testnet_smoke_evidence_key",
        table_name="lighter_testnet_smoke_evidence",
    )
    op.drop_table("lighter_testnet_smoke_evidence")
