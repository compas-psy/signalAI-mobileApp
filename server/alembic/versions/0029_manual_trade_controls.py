"""Persist durable monotonic owner controls for open trades.

revision = 0029_manual_trade_controls
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0029_manual_trade_controls"
down_revision = "0028_management_policy_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_manual_trade_controls",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "management_policy_snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'REQUESTED'"),
            nullable=False,
        ),
        sa.Column("idempotency_key_sha256", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_quantity", sa.Numeric(28, 12), nullable=True),
        sa.Column("requested_stop", sa.Numeric(28, 12), nullable=True),
        sa.Column(
            "reduce_only",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('CLOSE','REDUCE','TIGHTEN_STOP','RETURN_AUTO')",
            name=op.f("ck_execution_manual_trade_controls_known_action"),
        ),
        sa.CheckConstraint(
            "reduce_only IS TRUE",
            name=op.f("ck_execution_manual_trade_controls_always_reduce_only"),
        ),
        sa.CheckConstraint(
            "(action = 'CLOSE' AND requested_quantity IS NULL AND requested_stop IS NULL)"
            " OR (action = 'REDUCE' AND requested_quantity > 0 AND requested_stop IS NULL)"
            " OR (action = 'TIGHTEN_STOP' AND requested_quantity IS NULL AND requested_stop > 0)"
            " OR (action = 'RETURN_AUTO' AND requested_quantity IS NULL AND requested_stop IS NULL)",
            name=op.f("ck_execution_manual_trade_controls_payload_matches_action"),
        ),
        sa.ForeignKeyConstraint(
            ["intent_id"],
            ["execution_intents.id"],
            name=op.f(
                "fk_execution_manual_trade_controls_intent_id_execution_intents"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["management_policy_snapshot_id"],
            ["execution_management_policy_snapshots.id"],
            name=op.f(
                "fk_execution_manual_trade_controls_management_policy_snapshot_id_execution_management_policy_snapshots"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["execution_orders.id"],
            name=op.f(
                "fk_execution_manual_trade_controls_order_id_execution_orders"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_execution_manual_trade_controls"),
        ),
        sa.UniqueConstraint(
            "intent_id",
            "idempotency_key_sha256",
            name="uq_execution_manual_trade_controls_intent_idempotency",
        ),
    )
    op.create_index(
        "ix_execution_manual_trade_controls_intent",
        "execution_manual_trade_controls",
        ["intent_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_execution_manual_trade_controls_status",
        "execution_manual_trade_controls",
        ["status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_manual_trade_controls_status",
        table_name="execution_manual_trade_controls",
    )
    op.drop_index(
        "ix_execution_manual_trade_controls_intent",
        table_name="execution_manual_trade_controls",
    )
    op.drop_table("execution_manual_trade_controls")
