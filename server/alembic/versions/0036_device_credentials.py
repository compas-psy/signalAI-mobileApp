"""Add verifier-only per-device credentials.

revision = 0039_device_credentials
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0039_device_credentials"
down_revision = "0038_promotion_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_credentials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("token_verifier", sa.String(64), nullable=False),
        sa.Column("issued_request_hash", sa.String(64), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_authenticated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "device_id",
            "generation",
            name="uq_device_credentials_generation",
        ),
        sa.UniqueConstraint("token_verifier", name="uq_device_credentials_verifier"),
        sa.UniqueConstraint("issued_request_hash", name="uq_device_credentials_request"),
        sa.CheckConstraint(
            "generation > 0",
            name=op.f("ck_device_credentials_generation_positive"),
        ),
        sa.CheckConstraint(
            "char_length(device_id) BETWEEN 16 AND 64",
            name=op.f("ck_device_credentials_device_id_width"),
        ),
        sa.CheckConstraint(
            "char_length(token_verifier) = 64",
            name=op.f("ck_device_credentials_verifier_width"),
        ),
        sa.CheckConstraint(
            "char_length(issued_request_hash) = 64",
            name=op.f("ck_device_credentials_request_hash_width"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata_json) = 'object' "
            "AND octet_length(metadata_json::text) <= 256",
            name=op.f("ck_device_credentials_metadata_bounded"),
        ),
    )
    op.create_index(
        "ix_device_credentials_active",
        "device_credentials",
        ["device_id", "revoked_at"],
    )
    op.create_index(
        "uq_device_credentials_one_active",
        "device_credentials",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_device_credentials_one_active", table_name="device_credentials")
    op.drop_index("ix_device_credentials_active", table_name="device_credentials")
    op.drop_table("device_credentials")
