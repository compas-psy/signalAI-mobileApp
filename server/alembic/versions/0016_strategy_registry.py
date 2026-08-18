"""Persist strategy registry identity and append-only promotion history.

revision = 0016_strategy_registry
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_strategy_registry"
down_revision = "0015_strategy_idea_provenance"
branch_labels = None
depends_on = None

LEGACY_VERSION = "legacy_control_v1"
LEGACY_CONFIG_HASH = "110d5b5d29560e762f2ee15528bd03ed6ae30b0e6a652b94a40b40eeabd51ada"
LEGACY_FAMILIES = ("TREND_PULLBACK", "BREAKOUT_RETEST", "WYCKOFF_REVERSAL")


def upgrade() -> None:
    op.create_table(
        "strategy_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("family", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "venue_allowlist",
            postgresql.ARRAY(sa.String(length=16)),
            nullable=False,
        ),
        sa.Column(
            "instrument_prefixes",
            postgresql.ARRAY(sa.String(length=64)),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_strategy_versions"),
        sa.UniqueConstraint(
            "family", "version", name="uq_strategy_versions_family_version"
        ),
    )
    op.create_index(
        "ix_strategy_versions_family", "strategy_versions", ["family"], unique=False
    )

    op.create_table(
        "strategy_promotion_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("strategy_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("from_role", sa.String(length=16), nullable=True),
        sa.Column("to_role", sa.String(length=16), nullable=False),
        sa.Column(
            "enabled_stages",
            postgresql.ARRAY(sa.String(length=24)),
            nullable=False,
        ),
        sa.Column("ui_visible", sa.Boolean(), nullable=False),
        sa.Column("decision_ref", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "detail_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"],
            ["strategy_versions.id"],
            name="fk_strategy_promotion_events_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_strategy_promotion_events"),
        sa.UniqueConstraint(
            "strategy_version_id",
            "sequence",
            name="uq_strategy_promotion_events_sequence",
        ),
    )
    op.create_index(
        "ix_strategy_promotion_events_time",
        "strategy_promotion_events",
        ["occurred_at"],
        unique=False,
    )

    for family in LEGACY_FAMILIES:
        op.execute(
            sa.text(
                """
                INSERT INTO strategy_versions
                    (family, version, config_hash, venue_allowlist, instrument_prefixes)
                VALUES
                    (:family, :version, :config_hash,
                     ARRAY['MOEX','CRYPTO']::VARCHAR(16)[],
                     ARRAY[]::VARCHAR(64)[])
                """
            ).bindparams(
                family=family,
                version=LEGACY_VERSION,
                config_hash=LEGACY_CONFIG_HASH,
            )
        )

    op.execute(
        sa.text(
            """
            INSERT INTO strategy_promotion_events
                (strategy_version_id, sequence, actor, event_type,
                 from_role, to_role, enabled_stages, ui_visible,
                 decision_ref, reason, detail_json)
            SELECT
                id,
                1,
                'system',
                'REGISTERED',
                NULL,
                'CONTROL',
                ARRAY['BACKTEST','OOS','SHADOW','PAPER','SANDBOX']::VARCHAR(24)[],
                TRUE,
                'migration:0016_strategy_registry',
                'Seed immutable legacy control registry identity',
                '{}'::jsonb
            FROM strategy_versions
            WHERE version = :version
            """
        ).bindparams(version=LEGACY_VERSION)
    )

    # Reuse the database-level append-only function introduced in 0002. Both
    # identity and governance history are immutable: corrections are new rows,
    # never destructive rewrites.
    op.execute(
        """
        CREATE TRIGGER strategy_versions_append_only
        BEFORE UPDATE OR DELETE ON strategy_versions
        FOR EACH ROW EXECUTE FUNCTION signalai_append_only();
        """
    )
    op.execute(
        """
        CREATE TRIGGER strategy_promotion_events_append_only
        BEFORE UPDATE OR DELETE ON strategy_promotion_events
        FOR EACH ROW EXECUTE FUNCTION signalai_append_only();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS strategy_promotion_events_append_only "
        "ON strategy_promotion_events;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS strategy_versions_append_only ON strategy_versions;"
    )
    op.drop_index(
        "ix_strategy_promotion_events_time", table_name="strategy_promotion_events"
    )
    op.drop_table("strategy_promotion_events")
    op.drop_index("ix_strategy_versions_family", table_name="strategy_versions")
    op.drop_table("strategy_versions")
