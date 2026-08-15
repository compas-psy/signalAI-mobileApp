"""Persist as-of research evidence used by portfolio screening.

revision = 0014_portfolio_research_evidence
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_portfolio_research_evidence"
down_revision = "0013_equity_ranking_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portfolio_weights",
        sa.Column(
            "evidence_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("portfolio_weights", "evidence_json")
