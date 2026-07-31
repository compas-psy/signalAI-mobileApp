"""Курс котировочной валюты к рублю (§17.1).

Размер позиции считался делением рублёвого бюджета риска на риск одной
единицы инструмента — и у крипты этот риск в USDT. Рубли делились на
доллары, результат подписывался «штук», и позиция выходила в восемьдесят
раз больше допустимой.

Курс — это данные, а не константа: его нужно снимать, хранить со временем
снятия и отказываться считать, когда он устарел.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_fx_rates"
down_revision = "0006_package_meets_target"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fx_rates",
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("rate_rub", sa.Numeric(24, 8), nullable=False),
        sa.Column(
            "as_of",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=64), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("currency", name="pk_fx_rates"),
        sa.CheckConstraint("rate_rub > 0", name="ck_fx_rates_rate_positive"),
    )


def downgrade() -> None:
    op.drop_table("fx_rates")
