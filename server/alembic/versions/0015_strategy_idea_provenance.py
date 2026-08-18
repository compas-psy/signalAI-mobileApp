"""Persist strategy provenance on every trade idea.

revision = 0015_strategy_idea_provenance
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_strategy_idea_provenance"
down_revision = "0014_portfolio_research_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trade_ideas",
        sa.Column("strategy_family", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "trade_ideas",
        sa.Column("strategy_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "trade_ideas",
        sa.Column("strategy_role", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "trade_ideas",
        sa.Column("strategy_config_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "trade_ideas",
        sa.Column("strategy_code_ref", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "trade_ideas",
        sa.Column("risk_policy_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "trade_ideas",
        sa.Column("generated_stage", sa.String(length=24), nullable=True),
    )

    # Historical rows predate exact strategy provenance. Family is known from
    # the immutable strategy column; everything else is deliberately marked
    # unknown instead of fabricating exact historical attribution.
    op.execute("UPDATE trade_ideas SET strategy_family = strategy")
    op.execute("UPDATE trade_ideas SET strategy_version = 'legacy_unknown'")
    op.execute("UPDATE trade_ideas SET strategy_role = 'legacy_unknown'")
    op.execute("UPDATE trade_ideas SET strategy_config_hash = 'legacy_unknown'")
    op.execute("UPDATE trade_ideas SET strategy_code_ref = 'legacy_unknown'")
    op.execute("UPDATE trade_ideas SET risk_policy_version = 'legacy_unknown'")
    op.execute("UPDATE trade_ideas SET generated_stage = 'legacy_unknown'")

    op.alter_column("trade_ideas", "strategy_family", nullable=False)
    op.alter_column("trade_ideas", "strategy_version", nullable=False)
    op.alter_column("trade_ideas", "strategy_role", nullable=False)
    op.alter_column("trade_ideas", "strategy_config_hash", nullable=False)
    op.alter_column("trade_ideas", "strategy_code_ref", nullable=False)
    op.alter_column("trade_ideas", "risk_policy_version", nullable=False)
    op.alter_column("trade_ideas", "generated_stage", nullable=False)


def downgrade() -> None:
    op.drop_column("trade_ideas", "generated_stage")
    op.drop_column("trade_ideas", "risk_policy_version")
    op.drop_column("trade_ideas", "strategy_code_ref")
    op.drop_column("trade_ideas", "strategy_config_hash")
    op.drop_column("trade_ideas", "strategy_role")
    op.drop_column("trade_ideas", "strategy_version")
    op.drop_column("trade_ideas", "strategy_family")
