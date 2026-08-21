"""Persist append-only Lighter reconciliation evidence.

revision = 0035_lighter_reconciliation
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035_lighter_reconciliation"
down_revision = "0034_lighter_protection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lighter_reconciliation_evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("evidence_key", sa.String(length=64), nullable=False),
        sa.Column("action_key", sa.String(length=192), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("account_index", sa.BigInteger(), nullable=False),
        sa.Column("api_key_index", sa.Integer(), nullable=False),
        sa.Column("reserved_nonce", sa.BigInteger(), nullable=False),
        sa.Column("provider_next_nonce", sa.BigInteger(), nullable=False),
        sa.Column("provider_order_id", sa.String(length=128), nullable=True),
        sa.Column("provider_order_status", sa.String(length=64), nullable=True),
        sa.Column("provider_tx_hash", sa.String(length=128), nullable=True),
        sa.Column("provider_tx_status", sa.Integer(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('ORDER_FOUND','TX_FOUND','AMBIGUOUS','CONSUMED_UNKNOWN')",
            name=op.f("ck_lighter_reconciliation_evidence_outcome_valid"),
        ),
        sa.CheckConstraint(
            "account_index >= 0",
            name=op.f("ck_lighter_reconciliation_evidence_account_index_non_negative"),
        ),
        sa.CheckConstraint(
            "api_key_index >= 0 AND api_key_index <= 253",
            name=op.f("ck_lighter_reconciliation_evidence_api_key_index_range"),
        ),
        sa.CheckConstraint(
            "reserved_nonce >= 0",
            name=op.f("ck_lighter_reconciliation_evidence_reserved_nonce_non_negative"),
        ),
        sa.CheckConstraint(
            "provider_next_nonce >= reserved_nonce",
            name=op.f("ck_lighter_reconciliation_evidence_provider_nonce_floor"),
        ),
        sa.CheckConstraint(
            "char_length(evidence_key) = 64",
            name=op.f("ck_lighter_reconciliation_evidence_evidence_key_sha256_width"),
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_lighter_reconciliation_evidence"),
        ),
    )
    op.create_index(
        "uq_lighter_reconciliation_evidence_key",
        "lighter_reconciliation_evidence",
        ["evidence_key"],
        unique=True,
    )
    op.create_index(
        "ix_lighter_reconciliation_evidence_action",
        "lighter_reconciliation_evidence",
        ["action_key", "observed_at"],
        unique=False,
    )
    op.execute(
        "CREATE TRIGGER trg_lighter_reconciliation_evidence_append_only "
        "BEFORE UPDATE OR DELETE ON lighter_reconciliation_evidence "
        "FOR EACH ROW EXECUTE FUNCTION signalai_append_only()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_lighter_reconciliation_evidence_append_only "
        "ON lighter_reconciliation_evidence"
    )
    op.drop_index(
        "ix_lighter_reconciliation_evidence_action",
        table_name="lighter_reconciliation_evidence",
    )
    op.drop_index(
        "uq_lighter_reconciliation_evidence_key",
        table_name="lighter_reconciliation_evidence",
    )
    op.drop_table("lighter_reconciliation_evidence")
