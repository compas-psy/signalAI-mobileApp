"""Persist explicit rebalance economics status and provenance.

revision = 0036_rebalance_economics
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036_rebalance_economics"
down_revision = "0037_lighter_smoke_event_clock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "rebalance_drafts",
        "estimated_costs",
        existing_type=sa.Numeric(precision=24, scale=8),
        nullable=True,
    )
    op.add_column(
        "rebalance_drafts",
        sa.Column(
            "economics_status",
            sa.String(length=16),
            nullable=False,
            server_default="UNKNOWN",
        ),
    )
    op.add_column(
        "rebalance_drafts",
        sa.Column(
            "economics_provenance_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "rebalance_drafts",
        sa.Column("broker_final_costs", sa.Numeric(precision=24, scale=8), nullable=True),
    )
    op.add_column(
        "rebalance_drafts",
        sa.Column("broker_final_tax", sa.Numeric(precision=24, scale=8), nullable=True),
    )
    # Historic zero was a placeholder, not a known zero-cost broker result.
    op.execute(
        "UPDATE rebalance_drafts "
        "SET estimated_costs = NULL, estimated_tax = NULL, "
        "tax_is_estimate = false, economics_status = 'UNKNOWN', "
        "economics_provenance_json = '{}'::jsonb"
    )
    op.alter_column("rebalance_drafts", "economics_status", server_default=None)
    op.alter_column("rebalance_drafts", "economics_provenance_json", server_default=None)
    op.create_check_constraint(
        op.f("ck_rebalance_drafts_economics_status_known"),
        "rebalance_drafts",
        "economics_status IN ('UNKNOWN','ESTIMATED','BROKER_FINAL')",
    )
    op.create_check_constraint(
        op.f("ck_rebalance_drafts_estimated_costs_non_negative"),
        "rebalance_drafts",
        "estimated_costs IS NULL OR estimated_costs >= 0",
    )
    op.create_check_constraint(
        op.f("ck_rebalance_drafts_estimated_tax_non_negative"),
        "rebalance_drafts",
        "estimated_tax IS NULL OR estimated_tax >= 0",
    )
    op.create_check_constraint(
        op.f("ck_rebalance_drafts_broker_final_costs_non_negative"),
        "rebalance_drafts",
        "broker_final_costs IS NULL OR broker_final_costs >= 0",
    )
    op.create_check_constraint(
        op.f("ck_rebalance_drafts_broker_final_tax_non_negative"),
        "rebalance_drafts",
        "broker_final_tax IS NULL OR broker_final_tax >= 0",
    )


def downgrade() -> None:
    for constraint in (
        "ck_rebalance_drafts_broker_final_tax_non_negative",
        "ck_rebalance_drafts_broker_final_costs_non_negative",
        "ck_rebalance_drafts_estimated_tax_non_negative",
        "ck_rebalance_drafts_estimated_costs_non_negative",
        "ck_rebalance_drafts_economics_status_known",
    ):
        op.drop_constraint(op.f(constraint), "rebalance_drafts", type_="check")
    op.execute(
        "UPDATE rebalance_drafts SET estimated_costs = COALESCE(estimated_costs, 0)"
    )
    op.drop_column("rebalance_drafts", "broker_final_tax")
    op.drop_column("rebalance_drafts", "broker_final_costs")
    op.drop_column("rebalance_drafts", "economics_provenance_json")
    op.drop_column("rebalance_drafts", "economics_status")
    op.alter_column(
        "rebalance_drafts",
        "estimated_costs",
        existing_type=sa.Numeric(precision=24, scale=8),
        nullable=False,
    )
