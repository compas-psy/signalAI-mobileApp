"""Persist provider-confirmed T-Invest Sandbox round-trip readiness.

revision = 0044_tinvest_sandbox_roundtrip
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044_tinvest_sandbox_roundtrip"
down_revision = "0043_lighter_submit_ambiguity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tinvest_sandbox_roundtrip_proofs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("source_sha", sa.String(length=40), nullable=False),
        sa.Column("credential_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("account_suffix", sa.String(length=32), nullable=False),
        sa.Column("buy_order_id", sa.String(length=64), nullable=False),
        sa.Column("buy_status", sa.String(length=64), nullable=False),
        sa.Column("buy_executed_lots", sa.Integer(), nullable=False),
        sa.Column("sell_order_id", sa.String(length=64), nullable=False),
        sa.Column("sell_status", sa.String(length=64), nullable=False),
        sa.Column("sell_executed_lots", sa.Integer(), nullable=False),
        sa.Column("position_flat", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "buy_executed_lots > 0",
            name=op.f("ck_tinvest_sandbox_roundtrip_proofs_buy_lots_positive"),
        ),
        sa.CheckConstraint(
            "position_flat = true",
            name=op.f("ck_tinvest_sandbox_roundtrip_proofs_position_must_be_flat"),
        ),
        sa.CheckConstraint(
            "sell_executed_lots = buy_executed_lots",
            name=op.f("ck_tinvest_sandbox_roundtrip_proofs_sell_matches_buy_lots"),
        ),
        sa.CheckConstraint(
            "source_sha ~ '^[0-9a-f]{40}$'",
            name=op.f("ck_tinvest_sandbox_roundtrip_proofs_source_sha_git_width"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tinvest_sandbox_roundtrip_proofs")),
    )
    op.create_index(
        "uq_tinvest_sandbox_roundtrip_release_credential",
        "tinvest_sandbox_roundtrip_proofs",
        ["source_sha", "credential_updated_at"],
        unique=True,
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION reject_tinvest_sandbox_roundtrip_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'tinvest sandbox round-trip proof is append-only';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER trg_tinvest_sandbox_roundtrip_append_only
        BEFORE UPDATE OR DELETE ON tinvest_sandbox_roundtrip_proofs
        FOR EACH ROW EXECUTE FUNCTION reject_tinvest_sandbox_roundtrip_mutation()
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_tinvest_sandbox_roundtrip_append_only "
        "ON tinvest_sandbox_roundtrip_proofs"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_tinvest_sandbox_roundtrip_mutation()")
    op.drop_index(
        "uq_tinvest_sandbox_roundtrip_release_credential",
        table_name="tinvest_sandbox_roundtrip_proofs",
    )
    op.drop_table("tinvest_sandbox_roundtrip_proofs")
