"""Persist append-only research search campaigns, trials and outcomes.

revision = 0018_multiple_testing_registry
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018_multiple_testing_registry"
down_revision = "0017_dataset_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_search_campaigns",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("hypothesis_id", sa.String(length=128), nullable=False),
        sa.Column("dataset_name", sa.String(length=96), nullable=False),
        sa.Column("dataset_snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_family", sa.String(length=32), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("planned_variant_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "planned_variant_count > 0",
            name=op.f("ck_research_search_campaigns_planned_variant_count_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_research_search_campaigns"),
    )
    op.create_index(
        "ix_research_search_campaigns_hypothesis",
        "research_search_campaigns",
        ["hypothesis_id"],
        unique=False,
    )
    op.create_index(
        "ix_research_search_campaigns_started",
        "research_search_campaigns",
        ["started_at"],
        unique=False,
    )

    op.create_table(
        "research_trials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("parameter_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "parameter_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ordinal > 0",
            name=op.f("ck_research_trials_ordinal_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["research_search_campaigns.id"],
            name="fk_research_trials_campaign_id_research_search_campaigns",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_research_trials"),
        sa.UniqueConstraint(
            "campaign_id",
            "ordinal",
            name="uq_research_trials_campaign_ordinal",
        ),
        sa.UniqueConstraint(
            "campaign_id",
            "parameter_hash",
            name="uq_research_trials_campaign_parameter_hash",
        ),
    )
    op.create_index(
        "ix_research_trials_campaign",
        "research_trials",
        ["campaign_id"],
        unique=False,
    )

    op.create_table(
        "research_trial_outcomes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("trial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("primary_metric", sa.Numeric(28, 12), nullable=True),
        sa.Column(
            "outcome_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('COMPLETED','FAILED','INVALID')",
            name=op.f("ck_research_trial_outcomes_status_terminal"),
        ),
        sa.ForeignKeyConstraint(
            ["trial_id"],
            ["research_trials.id"],
            name="fk_research_trial_outcomes_trial_id_research_trials",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_research_trial_outcomes"),
        sa.UniqueConstraint(
            "trial_id",
            name="uq_research_trial_outcomes_trial_id",
        ),
    )
    op.create_index(
        "ix_research_trial_outcomes_completed",
        "research_trial_outcomes",
        ["completed_at"],
        unique=False,
    )

    for table in (
        "research_search_campaigns",
        "research_trials",
        "research_trial_outcomes",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION signalai_append_only();
            """
        )


def downgrade() -> None:
    for table in (
        "research_trial_outcomes",
        "research_trials",
        "research_search_campaigns",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table};")

    op.drop_index(
        "ix_research_trial_outcomes_completed",
        table_name="research_trial_outcomes",
    )
    op.drop_table("research_trial_outcomes")
    op.drop_index("ix_research_trials_campaign", table_name="research_trials")
    op.drop_table("research_trials")
    op.drop_index(
        "ix_research_search_campaigns_started",
        table_name="research_search_campaigns",
    )
    op.drop_index(
        "ix_research_search_campaigns_hypothesis",
        table_name="research_search_campaigns",
    )
    op.drop_table("research_search_campaigns")
