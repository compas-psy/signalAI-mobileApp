"""Persist durable server execution domain and append-only audit facts.

revision = 0020_execution_domain
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020_execution_domain"
down_revision = "0019_experiments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_mode_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=12), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_execution_mode_state_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_mode_state")),
    )

    op.create_table(
        "execution_mode_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("from_mode", sa.String(length=12), nullable=True),
        sa.Column("to_mode", sa.String(length=12), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("detail_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_mode_events")),
    )
    op.create_index(
        "ix_execution_mode_events_occurred",
        "execution_mode_events",
        ["occurred_at"],
        unique=False,
    )

    op.create_table(
        "execution_intents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("idea_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("risk_policy_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("risk_override_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("venue", sa.String(length=32), nullable=False),
        sa.Column("account", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("planned_quantity", sa.Numeric(28, 12), nullable=False),
        sa.Column("planned_entry_price", sa.Numeric(28, 12), nullable=False),
        sa.Column("planned_stop_price", sa.Numeric(28, 12), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["idea_id"],
            ["trade_ideas.id"],
            name=op.f("fk_execution_intents_idea_id_trade_ideas"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.instrument_id"],
            name=op.f("fk_execution_intents_instrument_id_instruments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["risk_policy_snapshot_id"],
            ["risk_snapshots.id"],
            name=op.f("fk_execution_intents_risk_policy_snapshot_id_risk_snapshots"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_intents")),
    )
    op.create_index("ix_execution_intents_state", "execution_intents", ["state"], unique=False)
    op.create_index("ix_execution_intents_idea", "execution_intents", ["idea_id"], unique=False)
    op.create_index(
        "ix_execution_intents_venue_account_instrument",
        "execution_intents",
        ["venue", "account", "instrument_id"],
        unique=False,
    )

    op.create_table(
        "execution_orders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_order_id", sa.String(length=96), nullable=False),
        sa.Column("provider_order_id", sa.String(length=128), nullable=True),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("order_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 12), nullable=False),
        sa.Column("limit_price", sa.Numeric(28, 12), nullable=True),
        sa.Column("stop_price", sa.Numeric(28, 12), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["intent_id"],
            ["execution_intents.id"],
            name=op.f("fk_execution_orders_intent_id_execution_intents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_orders")),
        sa.UniqueConstraint("client_order_id", name="uq_execution_orders_client_order_id"),
    )
    op.create_index("ix_execution_orders_intent", "execution_orders", ["intent_id"], unique=False)
    op.create_index(
        "ix_execution_orders_provider_order",
        "execution_orders",
        ["provider_order_id"],
        unique=False,
    )

    op.create_table(
        "execution_fills",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_fill_id", sa.String(length=128), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 12), nullable=False),
        sa.Column("price", sa.Numeric(28, 12), nullable=False),
        sa.Column("fee_amount", sa.Numeric(24, 8), nullable=False),
        sa.Column("fee_currency", sa.String(length=16), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_execution_fills_positive_quantity")),
        sa.ForeignKeyConstraint(
            ["intent_id"],
            ["execution_intents.id"],
            name=op.f("fk_execution_fills_intent_id_execution_intents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["execution_orders.id"],
            name=op.f("fk_execution_fills_order_id_execution_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_fills")),
        sa.UniqueConstraint("order_id", "provider_fill_id", name="uq_execution_fills_provider"),
    )
    op.create_index("ix_execution_fills_intent", "execution_fills", ["intent_id"], unique=False)
    op.create_index("ix_execution_fills_filled", "execution_fills", ["filled_at"], unique=False)

    op.create_table(
        "execution_protections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("protection_type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_order_id", sa.String(length=128), nullable=True),
        sa.Column("quantity", sa.Numeric(28, 12), nullable=False),
        sa.Column("stop_price", sa.Numeric(28, 12), nullable=False),
        sa.Column("armed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
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
            "quantity > 0", name=op.f("ck_execution_protections_positive_quantity")
        ),
        sa.ForeignKeyConstraint(
            ["intent_id"],
            ["execution_intents.id"],
            name=op.f("fk_execution_protections_intent_id_execution_intents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["execution_orders.id"],
            name=op.f("fk_execution_protections_order_id_execution_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_protections")),
    )
    op.create_index(
        "ix_execution_protections_intent",
        "execution_protections",
        ["intent_id"],
        unique=False,
    )

    op.create_table(
        "execution_reconciliation_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("detail_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["intent_id"],
            ["execution_intents.id"],
            name=op.f("fk_execution_reconciliation_events_intent_id_execution_intents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_reconciliation_events")),
    )
    op.create_index(
        "ix_execution_reconciliation_intent",
        "execution_reconciliation_events",
        ["intent_id"],
        unique=False,
    )
    op.create_index(
        "ix_execution_reconciliation_occurred",
        "execution_reconciliation_events",
        ["occurred_at"],
        unique=False,
    )

    for table in (
        "execution_mode_events",
        "execution_fills",
        "execution_reconciliation_events",
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
        "execution_reconciliation_events",
        "execution_fills",
        "execution_mode_events",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table};")

    op.drop_index(
        "ix_execution_reconciliation_occurred",
        table_name="execution_reconciliation_events",
    )
    op.drop_index(
        "ix_execution_reconciliation_intent",
        table_name="execution_reconciliation_events",
    )
    op.drop_table("execution_reconciliation_events")
    op.drop_index("ix_execution_protections_intent", table_name="execution_protections")
    op.drop_table("execution_protections")
    op.drop_index("ix_execution_fills_filled", table_name="execution_fills")
    op.drop_index("ix_execution_fills_intent", table_name="execution_fills")
    op.drop_table("execution_fills")
    op.drop_index("ix_execution_orders_provider_order", table_name="execution_orders")
    op.drop_index("ix_execution_orders_intent", table_name="execution_orders")
    op.drop_table("execution_orders")
    op.drop_index(
        "ix_execution_intents_venue_account_instrument", table_name="execution_intents"
    )
    op.drop_index("ix_execution_intents_idea", table_name="execution_intents")
    op.drop_index("ix_execution_intents_state", table_name="execution_intents")
    op.drop_table("execution_intents")
    op.drop_index("ix_execution_mode_events_occurred", table_name="execution_mode_events")
    op.drop_table("execution_mode_events")
    op.drop_table("execution_mode_state")
