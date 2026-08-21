"""Persist isolated append-only Paper A/B decisions and outcomes.

revision = 0031_paper_ab_measurement
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031_paper_ab_measurement"
down_revision = "0030_shadow_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_ab_decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("decision_key", sa.String(length=64), nullable=False),
        sa.Column("pair_key", sa.String(length=64), nullable=False),
        sa.Column("candidate_version", sa.String(length=64), nullable=False),
        sa.Column("arm_role", sa.String(length=16), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("instrument_id", sa.String(length=64), nullable=False),
        sa.Column("venue", sa.String(length=32), nullable=False),
        sa.Column("regime", sa.String(length=64), nullable=False),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("cost_model_hash", sa.String(length=64), nullable=False),
        sa.Column("signal_emitted", sa.Boolean(), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=True),
        sa.Column("entry_reference", sa.Numeric(28, 12), nullable=True),
        sa.Column("confidence", sa.Numeric(12, 8), nullable=True),
        sa.Column("horizon_minutes", sa.Integer(), nullable=False),
        sa.Column("risk_unit_price", sa.Numeric(28, 12), nullable=True),
        sa.Column("round_trip_cost_bps", sa.Numeric(20, 8), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "arm_role IN ('CONTROL','CANDIDATE')",
            name=op.f("ck_paper_ab_decisions_paper_ab_arm_role_valid"),
        ),
        sa.CheckConstraint(
            "horizon_minutes > 0",
            name=op.f("ck_paper_ab_decisions_paper_ab_horizon_positive"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name=op.f("ck_paper_ab_decisions_paper_ab_confidence_probability"),
        ),
        sa.CheckConstraint(
            "risk_unit_price IS NULL OR risk_unit_price > 0",
            name=op.f("ck_paper_ab_decisions_paper_ab_risk_unit_positive"),
        ),
        sa.CheckConstraint(
            "round_trip_cost_bps IS NULL OR round_trip_cost_bps >= 0",
            name=op.f("ck_paper_ab_decisions_paper_ab_cost_non_negative"),
        ),
        sa.CheckConstraint(
            "(signal_emitted AND direction IN ('LONG','SHORT') AND entry_reference IS NOT NULL) "
            "OR (NOT signal_emitted AND direction IS NULL AND entry_reference IS NULL)",
            name=op.f("ck_paper_ab_decisions_paper_ab_signal_values_consistent"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_ab_decisions")),
        sa.UniqueConstraint("decision_key", name="uq_paper_ab_decisions_key"),
        sa.UniqueConstraint("pair_key", "arm_role", name="uq_paper_ab_decisions_pair_arm"),
    )
    op.create_index(
        "ix_paper_ab_decisions_candidate_time",
        "paper_ab_decisions",
        ["candidate_version", "decision_at"],
        unique=False,
    )
    op.create_index(
        "ix_paper_ab_decisions_pair",
        "paper_ab_decisions",
        ["pair_key"],
        unique=False,
    )

    op.create_table(
        "paper_ab_outcomes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_status", sa.String(length=24), nullable=False),
        sa.Column("net_r", sa.Numeric(28, 12), nullable=True),
        sa.Column("exit_reference", sa.Numeric(28, 12), nullable=True),
        sa.Column("outcome_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "evidence_status IN ('EVALUATED','INPUT_UNAVAILABLE')",
            name=op.f("ck_paper_ab_outcomes_paper_ab_outcome_status_valid"),
        ),
        sa.CheckConstraint(
            "(evidence_status = 'EVALUATED' AND reason_code IS NULL) OR "
            "(evidence_status = 'INPUT_UNAVAILABLE' AND reason_code IS NOT NULL "
            "AND net_r IS NULL AND exit_reference IS NULL)",
            name=op.f("ck_paper_ab_outcomes_paper_ab_outcome_reason_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["paper_ab_decisions.id"],
            name=op.f("fk_paper_ab_outcomes_decision_id_paper_ab_decisions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_ab_outcomes")),
        sa.UniqueConstraint("decision_id", name="uq_paper_ab_outcomes_decision"),
    )
    op.create_index(
        "ix_paper_ab_outcomes_time",
        "paper_ab_outcomes",
        ["outcome_at"],
        unique=False,
    )

    op.execute(
        "CREATE TRIGGER trg_paper_ab_decisions_append_only "
        "BEFORE UPDATE OR DELETE ON paper_ab_decisions "
        "FOR EACH ROW EXECUTE FUNCTION signalai_append_only()"
    )
    op.execute(
        "CREATE TRIGGER trg_paper_ab_outcomes_append_only "
        "BEFORE UPDATE OR DELETE ON paper_ab_outcomes "
        "FOR EACH ROW EXECUTE FUNCTION signalai_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_paper_ab_outcomes_append_only ON paper_ab_outcomes")
    op.execute("DROP TRIGGER IF EXISTS trg_paper_ab_decisions_append_only ON paper_ab_decisions")
    op.drop_index("ix_paper_ab_outcomes_time", table_name="paper_ab_outcomes")
    op.drop_table("paper_ab_outcomes")
    op.drop_index("ix_paper_ab_decisions_pair", table_name="paper_ab_decisions")
    op.drop_index("ix_paper_ab_decisions_candidate_time", table_name="paper_ab_decisions")
    op.drop_table("paper_ab_decisions")
