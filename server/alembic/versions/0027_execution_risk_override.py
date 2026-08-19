"""Persist immutable bounded execution risk overrides.

revision = 0027_execution_risk_override
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027_execution_risk_override"
down_revision = "0026_execution_intent_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_risk_overrides",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("idea_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("risk_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("preset", sa.String(length=24), nullable=False),
        sa.Column("venue", sa.String(length=32), nullable=False),
        sa.Column("account", sa.String(length=128), nullable=False),
        sa.Column("execution_mode_snapshot", sa.String(length=12), nullable=False),
        sa.Column("base_risk_pct", sa.Numeric(12, 8), nullable=False),
        sa.Column("effective_risk_pct", sa.Numeric(12, 8), nullable=False),
        sa.Column("hard_cap_risk_pct", sa.Numeric(12, 8), nullable=False),
        sa.Column("base_quantity", sa.Numeric(28, 12), nullable=False),
        sa.Column("effective_quantity", sa.Numeric(28, 12), nullable=False),
        sa.Column("effective_leverage", sa.Numeric(12, 8), nullable=True),
        sa.Column("hard_cap_leverage", sa.Numeric(12, 8), nullable=True),
        sa.Column("preview_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("actor", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "detail_json",
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
            "base_risk_pct > 0",
            name=op.f("ck_execution_risk_overrides_positive_base_risk_pct"),
        ),
        sa.CheckConstraint(
            "effective_risk_pct > 0",
            name=op.f("ck_execution_risk_overrides_positive_effective_risk_pct"),
        ),
        sa.CheckConstraint(
            "hard_cap_risk_pct > 0",
            name=op.f("ck_execution_risk_overrides_positive_hard_cap_risk_pct"),
        ),
        sa.CheckConstraint(
            "effective_risk_pct <= hard_cap_risk_pct",
            name=op.f("ck_execution_risk_overrides_effective_risk_within_cap"),
        ),
        sa.CheckConstraint(
            "base_quantity >= 0",
            name=op.f("ck_execution_risk_overrides_base_quantity_not_negative"),
        ),
        sa.CheckConstraint(
            "effective_quantity > 0",
            name=op.f("ck_execution_risk_overrides_positive_effective_quantity"),
        ),
        sa.CheckConstraint(
            "effective_leverage IS NULL OR effective_leverage > 0",
            name=op.f("ck_execution_risk_overrides_positive_effective_leverage"),
        ),
        sa.CheckConstraint(
            "hard_cap_leverage IS NULL OR hard_cap_leverage > 0",
            name=op.f("ck_execution_risk_overrides_positive_hard_cap_leverage"),
        ),
        sa.CheckConstraint(
            "effective_leverage IS NULL OR (hard_cap_leverage IS NOT NULL AND effective_leverage <= hard_cap_leverage)",
            name=op.f("ck_execution_risk_overrides_effective_leverage_within_cap"),
        ),
        sa.ForeignKeyConstraint(
            ["idea_id"],
            ["trade_ideas.id"],
            name=op.f("fk_execution_risk_overrides_idea_id_trade_ideas"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["risk_snapshot_id"],
            ["risk_snapshots.id"],
            name=op.f(
                "fk_execution_risk_overrides_risk_snapshot_id_risk_snapshots"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_risk_overrides")),
        sa.UniqueConstraint(
            "preview_hash",
            name="uq_execution_risk_overrides_preview_hash",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_execution_risk_overrides_idempotency_key",
        ),
    )
    op.create_index(
        "ix_execution_risk_overrides_idea",
        "execution_risk_overrides",
        ["idea_id", "created_at"],
        unique=False,
    )
    op.execute(
        """
        CREATE TRIGGER execution_risk_overrides_append_only
        BEFORE UPDATE OR DELETE ON execution_risk_overrides
        FOR EACH ROW EXECUTE FUNCTION signalai_append_only();
        """
    )
    op.create_foreign_key(
        op.f("fk_execution_intents_risk_override_id_execution_risk_overrides"),
        "execution_intents",
        "execution_risk_overrides",
        ["risk_override_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_execution_intents_risk_override_id_execution_risk_overrides"),
        "execution_intents",
        type_="foreignkey",
    )
    op.execute(
        "DROP TRIGGER IF EXISTS execution_risk_overrides_append_only "
        "ON execution_risk_overrides;"
    )
    op.drop_index(
        "ix_execution_risk_overrides_idea",
        table_name="execution_risk_overrides",
    )
    op.drop_table("execution_risk_overrides")
