"""Add durable retry scheduling and worker lease to execution intents.

revision = 0022_execution_retry_lease
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_execution_retry_lease"
down_revision = "0021_execution_intent_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "execution_intents",
        sa.Column(
            "retry_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "execution_intents",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "execution_intents",
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "execution_intents",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "ix_execution_intents_retry_due",
        "execution_intents",
        ["state", "next_retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_execution_intents_lease_expiry",
        "execution_intents",
        ["lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_intents_lease_expiry",
        table_name="execution_intents",
    )
    op.drop_index(
        "ix_execution_intents_retry_due",
        table_name="execution_intents",
    )
    op.drop_column("execution_intents", "lease_expires_at")
    op.drop_column("execution_intents", "lease_owner")
    op.drop_column("execution_intents", "next_retry_at")
    op.drop_column("execution_intents", "retry_count")
