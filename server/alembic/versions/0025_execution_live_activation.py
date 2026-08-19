"""Persist durable two-step LIVE activation challenges.

revision = 0025_execution_live_activation
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025_execution_live_activation"
down_revision = "0024_execution_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_mode_activation_requests",
        sa.Column(
            "preview_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("from_mode", sa.String(length=12), nullable=False),
        sa.Column("target_mode", sa.String(length=12), nullable=False),
        sa.Column("venue", sa.String(length=32), nullable=False),
        sa.Column("account", sa.String(length=128), nullable=False),
        sa.Column("capital_rub", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column(
            "hard_caps_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "blockers_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("outcome_mode", sa.String(length=12), nullable=True),
        sa.Column("owner_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "preview_hash",
            name="uq_execution_mode_activation_requests_preview_hash",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_execution_mode_activation_requests_idempotency_key",
        ),
    )
    op.create_index(
        "ix_execution_mode_activation_requests_created",
        "execution_mode_activation_requests",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_execution_mode_activation_requests_status",
        "execution_mode_activation_requests",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_mode_activation_requests_status",
        table_name="execution_mode_activation_requests",
    )
    op.drop_index(
        "ix_execution_mode_activation_requests_created",
        table_name="execution_mode_activation_requests",
    )
    op.drop_table("execution_mode_activation_requests")
