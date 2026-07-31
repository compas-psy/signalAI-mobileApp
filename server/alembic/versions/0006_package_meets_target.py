"""Пакет годен, но рискованнее профиля — это состояние, а не брак (§6.1).

Проверка на истории отвечала одним словом: допущен или нет. В это «нет»
попадало и «вне обучения состав теряет деньги», и «просадка вышла больше
целевой для профиля». Первое действительно делает состав бессмысленным.
Второе — свойство рынка: на истории, включающей 2022 год, целевую просадку
консервативного профиля не выдерживает почти ничто.

Склеенные вместе, они дали экран, который посчитал восемнадцать составов и не
показал ни одного. Владельцу нужны числа риска, а не пустота вместо них,
поэтому «не уложился в цель профиля» переезжает в признак рядом с составом.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_package_meets_target"
down_revision = "0005_portfolio_run_report"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portfolio_models",
        sa.Column(
            "meets_target", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.add_column(
        "portfolio_models",
        sa.Column(
            "warnings_json", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("portfolio_models", "warnings_json")
    op.drop_column("portfolio_models", "meets_target")
