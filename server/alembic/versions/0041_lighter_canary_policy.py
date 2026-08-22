"""Persist Lighter live credential generations and Canary policy snapshots.

revision = 0041_lighter_canary_policy
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0041_lighter_canary_policy"
down_revision = "0040_device_pairing_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lighter_credential_generations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("generation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slot", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("account_index", sa.Integer(), nullable=False),
        sa.Column("api_key_index", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "slot = 'lighter_trade'",
            name=op.f("ck_lighter_credential_generations_slot_live_trade_only"),
        ),
        sa.CheckConstraint(
            "action IN ('CREATED', 'ROTATED', 'REVOKED')",
            name=op.f("ck_lighter_credential_generations_action_valid"),
        ),
        sa.CheckConstraint(
            "char_length(actor) BETWEEN 1 AND 64",
            name=op.f("ck_lighter_credential_generations_actor_non_empty"),
        ),
        sa.CheckConstraint(
            "account_index >= 0",
            name=op.f("ck_lighter_credential_generations_account_index_non_negative"),
        ),
        sa.CheckConstraint(
            "api_key_index BETWEEN 0 AND 253",
            name=op.f("ck_lighter_credential_generations_api_key_index_bounded"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lighter_credential_generations")),
        sa.UniqueConstraint(
            "generation_id",
            name="uq_lighter_credential_generations_generation_id",
        ),
    )
    op.create_index(
        "ix_lighter_credential_generations_slot_id",
        "lighter_credential_generations",
        ["slot", "id"],
    )

    op.create_table(
        "canary_policy_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("source_sha", sa.String(length=40), nullable=False),
        sa.Column("engine_config_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "credential_generation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("account_index", sa.Integer(), nullable=False),
        sa.Column("api_key_index", sa.Integer(), nullable=False),
        sa.Column("strategy_family", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_version > 0",
            name=op.f("ck_canary_policy_snapshots_schema_version_positive"),
        ),
        sa.CheckConstraint(
            "char_length(snapshot_hash) = 64",
            name=op.f("ck_canary_policy_snapshots_snapshot_hash_sha256_width"),
        ),
        sa.CheckConstraint(
            "char_length(source_sha) = 40",
            name=op.f("ck_canary_policy_snapshots_source_sha_width"),
        ),
        sa.CheckConstraint(
            "char_length(engine_config_hash) = 64",
            name=op.f("ck_canary_policy_snapshots_config_hash_sha256_width"),
        ),
        sa.CheckConstraint(
            "account_index >= 0",
            name=op.f("ck_canary_policy_snapshots_account_index_non_negative"),
        ),
        sa.CheckConstraint(
            "api_key_index BETWEEN 0 AND 253",
            name=op.f("ck_canary_policy_snapshots_api_key_index_bounded"),
        ),
        sa.CheckConstraint(
            "char_length(actor) BETWEEN 1 AND 64",
            name=op.f("ck_canary_policy_snapshots_actor_non_empty"),
        ),
        sa.CheckConstraint(
            "char_length(correlation_id) BETWEEN 1 AND 128",
            name=op.f("ck_canary_policy_snapshots_correlation_non_empty"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload_json) = 'object'",
            name=op.f("ck_canary_policy_snapshots_payload_object"),
        ),
        sa.ForeignKeyConstraint(
            ["credential_generation_id"],
            ["lighter_credential_generations.generation_id"],
            name=op.f(
                "fk_canary_policy_snapshots_credential_generation_id_lighter_credential_generations"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canary_policy_snapshots")),
        sa.UniqueConstraint(
            "snapshot_hash",
            name="uq_canary_policy_snapshots_snapshot_hash",
        ),
    )
    op.create_index(
        "ix_canary_policy_snapshots_generation_created",
        "canary_policy_snapshots",
        ["credential_generation_id", "created_at"],
    )

    for table, trigger in (
        (
            "lighter_credential_generations",
            "trg_lighter_credential_generations_append_only",
        ),
        ("canary_policy_snapshots", "trg_canary_policy_snapshots_append_only"),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION signalai_append_only()"
        )


def downgrade() -> None:
    for table, trigger in (
        ("canary_policy_snapshots", "trg_canary_policy_snapshots_append_only"),
        (
            "lighter_credential_generations",
            "trg_lighter_credential_generations_append_only",
        ),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    op.drop_index(
        "ix_canary_policy_snapshots_generation_created",
        table_name="canary_policy_snapshots",
    )
    op.drop_table("canary_policy_snapshots")
    op.drop_index(
        "ix_lighter_credential_generations_slot_id",
        table_name="lighter_credential_generations",
    )
    op.drop_table("lighter_credential_generations")
