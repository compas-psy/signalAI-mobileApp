"""Persist execution health telemetry inputs.

revision = 0024_execution_health
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_execution_health"
down_revision = "0023_execution_kill_switch_level"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "execution_intents",
        sa.Column(
            "duplicate_prevention_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_table(
        "execution_venue_health",
        sa.Column("venue", sa.String(length=32), nullable=False),
        sa.Column("account", sa.String(length=128), nullable=False),
        sa.Column(
            "websocket_connected",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("last_websocket_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stale_after_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stale_after_seconds > 0",
            name="positive_stale_after_seconds",
        ),
        sa.PrimaryKeyConstraint("venue", "account"),
    )
    op.create_index(
        "ix_execution_venue_health_updated",
        "execution_venue_health",
        ["updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_venue_health_updated",
        table_name="execution_venue_health",
    )
    op.drop_table("execution_venue_health")
    op.drop_column("execution_intents", "duplicate_prevention_count")
