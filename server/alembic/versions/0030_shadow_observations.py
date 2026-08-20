"""Persist isolated append-only Shadow candidate observations.

revision = 0030_shadow_observations
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030_shadow_observations"
down_revision = "0029_manual_trade_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shadow_observations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("observation_key", sa.String(length=64), nullable=False),
        sa.Column("opportunity_key", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("instrument_id", sa.String(length=64), nullable=False),
        sa.Column("venue", sa.String(length=32), nullable=False),
        sa.Column("strategy_family", sa.String(length=32), nullable=True),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("signal_emitted", sa.Boolean(), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=True),
        sa.Column("raw_edge_score", sa.Numeric(28, 12), nullable=True),
        sa.Column("entry_reference", sa.Numeric(28, 12), nullable=True),
        sa.Column("data_quality_state", sa.String(length=24), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("cost_model_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "persisted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stage = 'SHADOW'",
            name=op.f("ck_shadow_observations_shadow_stage_only"),
        ),
        sa.CheckConstraint(
            "(signal_emitted AND direction IS NOT NULL AND raw_edge_score IS NOT NULL "
            "AND entry_reference IS NOT NULL AND data_quality_state IS NOT NULL) OR "
            "(NOT signal_emitted AND direction IS NULL AND raw_edge_score IS NULL "
            "AND entry_reference IS NULL AND data_quality_state IS NULL)",
            name=op.f("ck_shadow_observations_shadow_signal_values_consistent"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shadow_observations")),
        sa.UniqueConstraint(
            "observation_key",
            name="uq_shadow_observations_key",
        ),
    )
    op.create_index(
        "ix_shadow_observations_opportunity",
        "shadow_observations",
        ["opportunity_key", "strategy_version"],
        unique=False,
    )
    op.create_index(
        "ix_shadow_observations_evaluated",
        "shadow_observations",
        ["evaluated_at", "instrument_id"],
        unique=False,
    )
    op.execute(
        "CREATE TRIGGER trg_shadow_observations_append_only "
        "BEFORE UPDATE OR DELETE ON shadow_observations "
        "FOR EACH ROW EXECUTE FUNCTION signalai_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_shadow_observations_append_only ON shadow_observations")
    op.drop_index("ix_shadow_observations_evaluated", table_name="shadow_observations")
    op.drop_index("ix_shadow_observations_opportunity", table_name="shadow_observations")
    op.drop_table("shadow_observations")
