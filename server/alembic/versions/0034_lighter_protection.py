"""Allow immutable Lighter position-protection action bindings.

revision = 0034_lighter_protection
"""

from __future__ import annotations

from alembic import op

revision = "0034_lighter_protection"
down_revision = "0033_lighter_order_actions"
branch_labels = None
depends_on = None

_TABLE = "lighter_order_action_bindings"
_CONSTRAINT = "ck_lighter_order_action_bindings_action_type_valid"


def _constraint_name():
    # 0033 created this constraint with op.f(...), so the persisted DB name is
    # already finalized under the repository naming convention. Mark it final
    # here too; otherwise Alembic applies the convention a second time.
    return op.f(_CONSTRAINT)


def upgrade() -> None:
    name = _constraint_name()
    op.drop_constraint(name, _TABLE, type_="check")
    op.create_check_constraint(
        name,
        _TABLE,
        "action_type IN ('CREATE','CANCEL','REDUCE','PROTECT')",
    )


def downgrade() -> None:
    name = _constraint_name()
    op.drop_constraint(name, _TABLE, type_="check")
    op.create_check_constraint(
        name,
        _TABLE,
        "action_type IN ('CREATE','CANCEL','REDUCE')",
    )
