"""Use wall-clock insertion time for ordered append-only smoke evidence.

PostgreSQL ``now()`` is transaction-stable. SAI-077 can persist CANCEL_FAILED
and RECOVERY_SUCCESS inside one surrounding operator/test transaction, so a
transaction timestamp cannot prove event order. ``clock_timestamp()`` gives the
actual insertion instant while the immutable ``observed_at`` remains the
operator-supplied event time.
"""

from __future__ import annotations

from alembic import op

revision = "0037_lighter_smoke_event_clock"
down_revision = "0036_lighter_testnet_smoke"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE lighter_testnet_smoke_evidence "
        "ALTER COLUMN created_at SET DEFAULT clock_timestamp()"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE lighter_testnet_smoke_evidence "
        "ALTER COLUMN created_at SET DEFAULT now()"
    )
