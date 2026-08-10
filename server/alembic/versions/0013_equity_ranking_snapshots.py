"""Daily equity ranking snapshots for Portfolio → Signals.

revision = 0013_equity_ranking_snapshots
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_equity_ranking_snapshots"
down_revision = "0012_notification_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equity_ranking_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("market_day", sa.Date(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("universe_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scored_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "methodology",
            sa.String(length=32),
            nullable=False,
            server_default="equity_rank_v1",
        ),
        sa.Column(
            "items_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_equity_ranking_snapshots"),
        sa.UniqueConstraint(
            "market_day",
            name="uq_equity_ranking_snapshots_market_day",
        ),
    )


def downgrade() -> None:
    op.drop_table("equity_ranking_snapshots")
