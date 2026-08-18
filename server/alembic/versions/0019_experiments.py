"""Persist append-only champion/challenger experiment evidence.

revision = 0019_experiments
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019_experiments"
down_revision = "0018_multiple_testing_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("control_family", sa.String(length=32), nullable=False),
        sa.Column("control_version", sa.String(length=64), nullable=False),
        sa.Column("candidate_family", sa.String(length=32), nullable=False),
        sa.Column("candidate_version", sa.String(length=64), nullable=False),
        sa.Column("dataset_name", sa.String(length=96), nullable=False),
        sa.Column("dataset_snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("same_data_hash", sa.String(length=64), nullable=False),
        sa.Column("cost_model_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "cost_model_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "persisted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "NOT (control_family = candidate_family AND control_version = candidate_version)",
            name=op.f("ck_experiments_control_candidate_differ"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiments")),
    )
    op.create_index(
        "ix_experiments_created_at", "experiments", ["created_at"], unique=False
    )
    op.create_index("ix_experiments_stage", "experiments", ["stage"], unique=False)

    op.create_table(
        "experiment_arms",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("arm_role", sa.String(length=16), nullable=False),
        sa.Column("strategy_family", sa.String(length=32), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "arm_role IN ('CONTROL','CANDIDATE')",
            name=op.f("ck_experiment_arms_arm_role_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            name=op.f("fk_experiment_arms_experiment_id_experiments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiment_arms")),
        sa.UniqueConstraint(
            "experiment_id",
            "arm_role",
            name="uq_experiment_arms_experiment_role",
        ),
        sa.UniqueConstraint(
            "experiment_id",
            "strategy_family",
            "strategy_version",
            name="uq_experiment_arms_strategy",
        ),
    )
    op.create_index(
        "ix_experiment_arms_experiment",
        "experiment_arms",
        ["experiment_id"],
        unique=False,
    )

    op.create_table(
        "experiment_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_snapshot_id", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("same_data_hash", sa.String(length=64), nullable=False),
        sa.Column("cost_model_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "cost_model_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("sample_adequate", sa.Boolean(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sample_size >= 0",
            name=op.f("ck_experiment_runs_sample_size_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            name=op.f("fk_experiment_runs_experiment_id_experiments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiment_runs")),
    )
    op.create_index(
        "ix_experiment_runs_experiment",
        "experiment_runs",
        ["experiment_id"],
        unique=False,
    )
    op.create_index(
        "ix_experiment_runs_evaluated",
        "experiment_runs",
        ["evaluated_at"],
        unique=False,
    )

    op.create_table(
        "experiment_metrics",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("control_value", sa.Numeric(28, 12), nullable=False),
        sa.Column("candidate_value", sa.Numeric(28, 12), nullable=False),
        sa.Column("delta", sa.Numeric(28, 12), nullable=False),
        sa.Column("unit", sa.String(length=24), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["experiment_runs.id"],
            name=op.f("fk_experiment_metrics_run_id_experiment_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_experiment_metrics")),
        sa.UniqueConstraint("run_id", "name", name="uq_experiment_metrics_run_name"),
    )
    op.create_index(
        "ix_experiment_metrics_run",
        "experiment_metrics",
        ["run_id"],
        unique=False,
    )

    op.create_table(
        "promotion_decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "detail_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('OWNER','AUTOMATIC')",
            name=op.f("ck_promotion_decisions_source_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id"],
            ["experiments.id"],
            name=op.f("fk_promotion_decisions_experiment_id_experiments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["experiment_runs.id"],
            name=op.f("fk_promotion_decisions_run_id_experiment_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotion_decisions")),
    )
    op.create_index(
        "ix_promotion_decisions_experiment",
        "promotion_decisions",
        ["experiment_id"],
        unique=False,
    )
    op.create_index(
        "ix_promotion_decisions_decided",
        "promotion_decisions",
        ["decided_at"],
        unique=False,
    )

    op.execute(
        """
        CREATE TRIGGER experiments_append_only
        BEFORE UPDATE OR DELETE ON experiments
        FOR EACH ROW EXECUTE FUNCTION signalai_append_only();
        """
    )
    op.execute(
        """
        CREATE TRIGGER experiment_arms_append_only
        BEFORE UPDATE OR DELETE ON experiment_arms
        FOR EACH ROW EXECUTE FUNCTION signalai_append_only();
        """
    )
    op.execute(
        """
        CREATE TRIGGER experiment_runs_append_only
        BEFORE UPDATE OR DELETE ON experiment_runs
        FOR EACH ROW EXECUTE FUNCTION signalai_append_only();
        """
    )
    op.execute(
        """
        CREATE TRIGGER experiment_metrics_append_only
        BEFORE UPDATE OR DELETE ON experiment_metrics
        FOR EACH ROW EXECUTE FUNCTION signalai_append_only();
        """
    )
    op.execute(
        """
        CREATE TRIGGER promotion_decisions_append_only
        BEFORE UPDATE OR DELETE ON promotion_decisions
        FOR EACH ROW EXECUTE FUNCTION signalai_append_only();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS promotion_decisions_append_only ON promotion_decisions;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS experiment_metrics_append_only ON experiment_metrics;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS experiment_runs_append_only ON experiment_runs;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS experiment_arms_append_only ON experiment_arms;"
    )
    op.execute("DROP TRIGGER IF EXISTS experiments_append_only ON experiments;")

    op.drop_index("ix_promotion_decisions_decided", table_name="promotion_decisions")
    op.drop_index("ix_promotion_decisions_experiment", table_name="promotion_decisions")
    op.drop_table("promotion_decisions")
    op.drop_index("ix_experiment_metrics_run", table_name="experiment_metrics")
    op.drop_table("experiment_metrics")
    op.drop_index("ix_experiment_runs_evaluated", table_name="experiment_runs")
    op.drop_index("ix_experiment_runs_experiment", table_name="experiment_runs")
    op.drop_table("experiment_runs")
    op.drop_index("ix_experiment_arms_experiment", table_name="experiment_arms")
    op.drop_table("experiment_arms")
    op.drop_index("ix_experiments_stage", table_name="experiments")
    op.drop_index("ix_experiments_created_at", table_name="experiments")
    op.drop_table("experiments")
