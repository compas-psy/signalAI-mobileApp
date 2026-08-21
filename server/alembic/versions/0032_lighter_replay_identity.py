"""Persist Lighter stable order identity and replay-safe nonce reservations.

revision = 0032_lighter_replay_identity
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032_lighter_replay_identity"
down_revision = "0031_paper_ab_measurement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lighter_order_identities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("account_index", sa.BigInteger(), nullable=False),
        sa.Column("client_order_id", sa.String(length=96), nullable=False),
        sa.Column("client_order_index", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "account_index >= 0",
            name=op.f("ck_lighter_order_identities_account_index_non_negative"),
        ),
        sa.CheckConstraint(
            "client_order_index > 0",
            name=op.f("ck_lighter_order_identities_client_order_index_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lighter_order_identities")),
    )
    op.create_index(
        "uq_lighter_order_identities_client_order_id",
        "lighter_order_identities",
        ["client_order_id"],
        unique=True,
    )
    op.create_index(
        "uq_lighter_order_identities_account_client_index",
        "lighter_order_identities",
        ["account_index", "client_order_index"],
        unique=True,
    )

    op.create_table(
        "lighter_nonce_reservations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("account_index", sa.BigInteger(), nullable=False),
        sa.Column("api_key_index", sa.Integer(), nullable=False),
        sa.Column("replay_key", sa.String(length=192), nullable=False),
        sa.Column("nonce", sa.BigInteger(), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default=sa.text("'RESERVED'"),
            nullable=False,
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "account_index >= 0",
            name=op.f("ck_lighter_nonce_reservations_account_index_non_negative"),
        ),
        sa.CheckConstraint(
            "api_key_index >= 0 AND api_key_index <= 253",
            name=op.f("ck_lighter_nonce_reservations_api_key_index_range"),
        ),
        sa.CheckConstraint(
            "nonce >= 0",
            name=op.f("ck_lighter_nonce_reservations_nonce_non_negative"),
        ),
        sa.CheckConstraint(
            "state IN ('RESERVED','CONSUMED')",
            name=op.f("ck_lighter_nonce_reservations_state_valid"),
        ),
        sa.CheckConstraint(
            "(state = 'RESERVED' AND consumed_at IS NULL) OR "
            "(state = 'CONSUMED' AND consumed_at IS NOT NULL)",
            name=op.f("ck_lighter_nonce_reservations_state_consumed_at_consistent"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lighter_nonce_reservations")),
    )
    op.create_index(
        "uq_lighter_nonce_reservations_replay_key",
        "lighter_nonce_reservations",
        ["replay_key"],
        unique=True,
    )
    op.create_index(
        "uq_lighter_nonce_reservations_scope_nonce",
        "lighter_nonce_reservations",
        ["account_index", "api_key_index", "nonce"],
        unique=True,
    )
    op.create_index(
        "uq_lighter_nonce_reservations_active_scope",
        "lighter_nonce_reservations",
        ["account_index", "api_key_index"],
        unique=True,
        postgresql_where=sa.text("state = 'RESERVED'"),
    )
    op.create_index(
        "ix_lighter_nonce_reservations_scope_state",
        "lighter_nonce_reservations",
        ["account_index", "api_key_index", "state"],
        unique=False,
    )

    op.execute(
        "CREATE TRIGGER trg_lighter_order_identities_append_only "
        "BEFORE UPDATE OR DELETE ON lighter_order_identities "
        "FOR EACH ROW EXECUTE FUNCTION signalai_append_only()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_lighter_order_identities_append_only "
        "ON lighter_order_identities"
    )
    op.drop_index(
        "ix_lighter_nonce_reservations_scope_state",
        table_name="lighter_nonce_reservations",
    )
    op.drop_index(
        "uq_lighter_nonce_reservations_active_scope",
        table_name="lighter_nonce_reservations",
    )
    op.drop_index(
        "uq_lighter_nonce_reservations_scope_nonce",
        table_name="lighter_nonce_reservations",
    )
    op.drop_index(
        "uq_lighter_nonce_reservations_replay_key",
        table_name="lighter_nonce_reservations",
    )
    op.drop_table("lighter_nonce_reservations")
    op.drop_index(
        "uq_lighter_order_identities_account_client_index",
        table_name="lighter_order_identities",
    )
    op.drop_index(
        "uq_lighter_order_identities_client_order_id",
        table_name="lighter_order_identities",
    )
    op.drop_table("lighter_order_identities")
