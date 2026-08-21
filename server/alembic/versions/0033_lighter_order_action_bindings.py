"""Persist immutable Lighter order action request bindings.

revision = 0033_lighter_order_actions
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033_lighter_order_actions"
down_revision = "0032_lighter_replay_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lighter_order_action_bindings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("action_key", sa.String(length=192), nullable=False),
        sa.Column("action_type", sa.String(length=16), nullable=False),
        sa.Column("account_index", sa.BigInteger(), nullable=False),
        sa.Column("api_key_index", sa.Integer(), nullable=False),
        sa.Column("client_order_id", sa.String(length=96), nullable=False),
        sa.Column("client_order_index", sa.BigInteger(), nullable=False),
        sa.Column("market_index", sa.Integer(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action_type IN ('CREATE','CANCEL','REDUCE')",
            name=op.f("ck_lighter_order_action_bindings_action_type_valid"),
        ),
        sa.CheckConstraint(
            "account_index >= 0",
            name=op.f("ck_lighter_order_action_bindings_account_index_non_negative"),
        ),
        sa.CheckConstraint(
            "api_key_index >= 0 AND api_key_index <= 253",
            name=op.f("ck_lighter_order_action_bindings_api_key_index_range"),
        ),
        sa.CheckConstraint(
            "client_order_index > 0",
            name=op.f("ck_lighter_order_action_bindings_client_order_index_positive"),
        ),
        sa.CheckConstraint(
            "market_index >= 0",
            name=op.f("ck_lighter_order_action_bindings_market_index_non_negative"),
        ),
        sa.CheckConstraint(
            "char_length(request_hash) = 64",
            name=op.f("ck_lighter_order_action_bindings_request_hash_sha256_width"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lighter_order_action_bindings")),
    )
    op.create_index(
        "uq_lighter_order_action_bindings_action_key",
        "lighter_order_action_bindings",
        ["action_key"],
        unique=True,
    )
    op.create_index(
        "ix_lighter_order_action_bindings_scope",
        "lighter_order_action_bindings",
        ["account_index", "api_key_index", "created_at"],
        unique=False,
    )
    op.execute(
        "CREATE TRIGGER trg_lighter_order_action_bindings_append_only "
        "BEFORE UPDATE OR DELETE ON lighter_order_action_bindings "
        "FOR EACH ROW EXECUTE FUNCTION signalai_append_only()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_lighter_order_action_bindings_append_only "
        "ON lighter_order_action_bindings"
    )
    op.drop_index(
        "ix_lighter_order_action_bindings_scope",
        table_name="lighter_order_action_bindings",
    )
    op.drop_index(
        "uq_lighter_order_action_bindings_action_key",
        table_name="lighter_order_action_bindings",
    )
    op.drop_table("lighter_order_action_bindings")
