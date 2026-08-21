"""Allow immutable Lighter position-protection action bindings.

revision = 0034_lighter_protection
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_lighter_protection"
down_revision = "0033_lighter_order_actions"
branch_labels = None
depends_on = None

_TABLE = "lighter_order_action_bindings"
_CONSTRAINT = "ck_lighter_order_action_bindings_action_type_valid"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "action_type IN ('CREATE','CANCEL','REDUCE','PROTECT')",
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "action_type IN ('CREATE','CANCEL','REDUCE')",
    )
