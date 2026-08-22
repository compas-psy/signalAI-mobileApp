"""Block blind Lighter resubmission after provider I/O begins.

revision = 0043_lighter_submit_ambiguity
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0043_lighter_submit_ambiguity"
down_revision = "0042_canary_evidence_references"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "uq_lighter_nonce_reservations_active_scope",
        table_name="lighter_nonce_reservations",
    )
    op.drop_constraint(
        "ck_lighter_nonce_reservations_state_consumed_at_consistent",
        "lighter_nonce_reservations",
        type_="check",
    )
    op.drop_constraint(
        "ck_lighter_nonce_reservations_state_valid",
        "lighter_nonce_reservations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_lighter_nonce_reservations_state_valid",
        "lighter_nonce_reservations",
        "state IN ('RESERVED','SUBMITTING','CONSUMED')",
    )
    op.create_check_constraint(
        "ck_lighter_nonce_reservations_state_consumed_at_consistent",
        "lighter_nonce_reservations",
        "(state IN ('RESERVED','SUBMITTING') AND consumed_at IS NULL) OR "
        "(state = 'CONSUMED' AND consumed_at IS NOT NULL)",
    )
    op.create_index(
        "uq_lighter_nonce_reservations_active_scope",
        "lighter_nonce_reservations",
        ["account_index", "api_key_index"],
        unique=True,
        postgresql_where=sa.text("state IN ('RESERVED','SUBMITTING')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_lighter_nonce_reservations_active_scope",
        table_name="lighter_nonce_reservations",
    )
    op.drop_constraint(
        "ck_lighter_nonce_reservations_state_consumed_at_consistent",
        "lighter_nonce_reservations",
        type_="check",
    )
    op.drop_constraint(
        "ck_lighter_nonce_reservations_state_valid",
        "lighter_nonce_reservations",
        type_="check",
    )
    op.execute(
        "UPDATE lighter_nonce_reservations SET state = 'RESERVED' "
        "WHERE state = 'SUBMITTING'"
    )
    op.create_check_constraint(
        "ck_lighter_nonce_reservations_state_valid",
        "lighter_nonce_reservations",
        "state IN ('RESERVED','CONSUMED')",
    )
    op.create_check_constraint(
        "ck_lighter_nonce_reservations_state_consumed_at_consistent",
        "lighter_nonce_reservations",
        "(state = 'RESERVED' AND consumed_at IS NULL) OR "
        "(state = 'CONSUMED' AND consumed_at IS NOT NULL)",
    )
    op.create_index(
        "uq_lighter_nonce_reservations_active_scope",
        "lighter_nonce_reservations",
        ["account_index", "api_key_index"],
        unique=True,
        postgresql_where=sa.text("state = 'RESERVED'"),
    )
