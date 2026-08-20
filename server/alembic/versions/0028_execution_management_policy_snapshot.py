"""Persist immutable open-trade management policy snapshots.

revision = 0028_execution_management_policy_snapshot
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028_execution_management_policy_snapshot"
down_revision = "0027_execution_risk_override"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_management_policy_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column(
            "risk_policy_snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "risk_override_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "risk_policy_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "manual_override_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "exit_profile_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "venue_rules_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["intent_id"],
            ["execution_intents.id"],
            name=op.f(
                "fk_execution_management_policy_snapshots_intent_id_execution_intents"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["risk_policy_snapshot_id"],
            ["risk_snapshots.id"],
            name=op.f(
                "fk_execution_management_policy_snapshots_risk_policy_snapshot_id_risk_snapshots"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["risk_override_id"],
            ["execution_risk_overrides.id"],
            name=op.f(
                "fk_execution_management_policy_snapshots_risk_override_id_execution_risk_overrides"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_execution_management_policy_snapshots"),
        ),
        sa.UniqueConstraint(
            "intent_id",
            name="uq_execution_management_policy_snapshots_intent",
        ),
    )
    op.create_index(
        "ix_execution_management_policy_snapshots_created",
        "execution_management_policy_snapshots",
        ["created_at"],
        unique=False,
    )
    op.execute(
        """
        CREATE TRIGGER execution_management_policy_snapshots_append_only
        BEFORE UPDATE OR DELETE ON execution_management_policy_snapshots
        FOR EACH ROW EXECUTE FUNCTION signalai_append_only();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS execution_management_policy_snapshots_append_only "
        "ON execution_management_policy_snapshots;"
    )
    op.drop_index(
        "ix_execution_management_policy_snapshots_created",
        table_name="execution_management_policy_snapshots",
    )
    op.drop_table("execution_management_policy_snapshots")
