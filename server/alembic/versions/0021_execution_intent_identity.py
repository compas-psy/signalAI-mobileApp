"""Add stable idempotency identity to execution intents.

revision = 0021_execution_intent_identity
"""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "0021_execution_intent_identity"
down_revision = "0020_execution_domain"
branch_labels = None
depends_on = None


def _identity_hash(row) -> str:
    payload = {
        "account": row.account,
        "idea_id": str(row.idea_id),
        "risk_override_id": (
            str(row.risk_override_id) if row.risk_override_id is not None else None
        ),
        "risk_policy_snapshot_id": str(row.risk_policy_snapshot_id),
        "strategy_version": row.strategy_version,
        "venue": row.venue,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column(
        "execution_intents",
        sa.Column("identity_hash", sa.String(length=64), nullable=True),
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT id, idea_id, strategy_version, risk_policy_snapshot_id,
                   risk_override_id, venue, account
            FROM execution_intents
            ORDER BY id
            """
        )
    ).fetchall()
    for row in rows:
        bind.execute(
            sa.text(
                "UPDATE execution_intents SET identity_hash = :identity_hash WHERE id = :id"
            ),
            {"identity_hash": _identity_hash(row), "id": row.id},
        )

    op.alter_column("execution_intents", "identity_hash", nullable=False)
    op.create_unique_constraint(
        "uq_execution_intents_identity_hash",
        "execution_intents",
        ["identity_hash"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_execution_intents_identity_hash",
        "execution_intents",
        type_="unique",
    )
    op.drop_column("execution_intents", "identity_hash")
