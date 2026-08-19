"""Persist distinct execution kill-switch levels.

revision = 0023_execution_kill_switch_level
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_execution_kill_switch_level"
down_revision = "0022_execution_retry_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "risk_state",
        sa.Column(
            "kill_switch_level",
            sa.String(length=24),
            server_default=sa.text("'CLEAR'"),
            nullable=False,
        ),
    )
    # Preserve any halt persisted by pre-SAI-028 code. Old boolean=true means
    # the least destructive active level, never CLEAR.
    op.execute(
        """
        UPDATE risk_state
        SET kill_switch_level = 'HALT_NEW_ENTRIES'
        WHERE kill_switch IS TRUE
        """
    )


def downgrade() -> None:
    op.drop_column("risk_state", "kill_switch_level")
