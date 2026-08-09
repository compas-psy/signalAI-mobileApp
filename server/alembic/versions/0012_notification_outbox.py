"""Durable server-to-device notification outbox.

revision = 0012_notification_outbox
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_notification_outbox"
down_revision = "0011_one_trade_per_instrument"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signalai_notification_outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("dedup_key", sa.String(length=240), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), server_default="", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_signalai_notification_outbox"),
        sa.UniqueConstraint(
            "dedup_key",
            name="uq_signalai_notification_outbox_dedup_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("signalai_notification_outbox")
