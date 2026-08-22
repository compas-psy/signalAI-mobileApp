"""Persist bounded verifier-only bootstrap pairing sessions.

revision = 0040_device_pairing_sessions
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0040_device_pairing_sessions"
down_revision = "0039_device_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_pairing_sessions",
        sa.Column("session_verifier", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("session_verifier"),
        sa.CheckConstraint(
            "char_length(session_verifier) = 64",
            name="ck_device_pairing_sessions_verifier_width",
        ),
        sa.CheckConstraint(
            "max_uses BETWEEN 1 AND 16",
            name="ck_device_pairing_sessions_max_uses_bounded",
        ),
        sa.CheckConstraint(
            "uses BETWEEN 0 AND max_uses",
            name="ck_device_pairing_sessions_uses_bounded",
        ),
    )


def downgrade() -> None:
    op.drop_table("device_pairing_sessions")
