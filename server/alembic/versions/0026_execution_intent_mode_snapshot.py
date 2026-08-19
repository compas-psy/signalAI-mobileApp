"""Snapshot lifecycle mode on every execution intent.

revision = 0026_execution_intent_mode
"""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "0026_execution_intent_mode"
down_revision = "0025_execution_live_activation"
branch_labels = None
depends_on = None


def _identity_hash(row, *, include_mode: bool) -> str:
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
    if include_mode:
        payload["execution_mode_snapshot"] = row.execution_mode_snapshot
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _rows(bind):
    return bind.execute(
        sa.text(
            """
            SELECT id, idea_id, strategy_version, risk_policy_snapshot_id,
                   risk_override_id, venue, account, execution_mode_snapshot
            FROM execution_intents
            ORDER BY id
            """
        )
    ).fetchall()


def _replace_identity_hashes(bind, rows, *, include_mode: bool) -> None:
    hashes = [(_identity_hash(row, include_mode=include_mode), row.id) for row in rows]
    values = [identity_hash for identity_hash, _ in hashes]
    if len(values) != len(set(values)):
        raise RuntimeError(
            "execution intent identity would collide while changing mode snapshot semantics"
        )
    for identity_hash, intent_id in hashes:
        bind.execute(
            sa.text(
                "UPDATE execution_intents SET identity_hash = :identity_hash WHERE id = :id"
            ),
            {"identity_hash": identity_hash, "id": intent_id},
        )


def upgrade() -> None:
    op.add_column(
        "execution_intents",
        sa.Column("execution_mode_snapshot", sa.String(length=12), nullable=True),
    )
    bind = op.get_bind()

    # Every intent that predates server-owned lifecycle modes was created by the
    # paper-only execution path. Preserve that fact explicitly instead of
    # guessing a later mode from mutable singleton state.
    bind.execute(
        sa.text(
            "UPDATE execution_intents SET execution_mode_snapshot = 'PAPER' "
            "WHERE execution_mode_snapshot IS NULL"
        )
    )

    rows = _rows(bind)
    op.drop_constraint(
        "uq_execution_intents_identity_hash",
        "execution_intents",
        type_="unique",
    )
    _replace_identity_hashes(bind, rows, include_mode=True)
    op.create_unique_constraint(
        "uq_execution_intents_identity_hash",
        "execution_intents",
        ["identity_hash"],
    )
    op.alter_column(
        "execution_intents",
        "execution_mode_snapshot",
        existing_type=sa.String(length=12),
        nullable=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    rows = _rows(bind)

    # A post-SAI-035 database can legitimately contain the same decision in two
    # lifecycle modes. The old identity cannot represent both. Refuse a lossy
    # downgrade instead of silently merging money-bearing intents.
    old_hashes = [_identity_hash(row, include_mode=False) for row in rows]
    if len(old_hashes) != len(set(old_hashes)):
        raise RuntimeError(
            "cannot downgrade execution intent mode snapshot without identity collision"
        )

    op.drop_constraint(
        "uq_execution_intents_identity_hash",
        "execution_intents",
        type_="unique",
    )
    _replace_identity_hashes(bind, rows, include_mode=False)
    op.create_unique_constraint(
        "uq_execution_intents_identity_hash",
        "execution_intents",
        ["identity_hash"],
    )
    op.drop_column("execution_intents", "execution_mode_snapshot")
