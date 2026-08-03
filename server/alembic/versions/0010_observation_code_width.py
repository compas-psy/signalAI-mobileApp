"""Тип наблюдения перестаёт быть подписью (§8.2).

В колонку фиксированной ширины складывали человекочитаемое название
показателя прямо из ответа источника, и первое же длинное название уронило
весь шаг разведки:

    (psycopg.errors.StringDataRightTruncation)
    value too long for type character varying(48)
    'cbr:fx_usd_rub:Средний номинальный курс за период'

Сорок девять символов против сорока восьми. Отказ приходит на автосбросе
сессии, то есть уносит с собой всё, что шаг успел собрать, — и владелец
видит «в сигналах инвестиций так ничего и нет» при полностью исправном
сборе.

Ширина здесь — вторая линия обороны, не первая. Первая в
``research.codes``: код собирается так, чтобы влезать по построению, потому
что название показателя задаёт источник и надеяться на его краткость
нельзя. Но запас нужен и в базе: девяносто шесть символов дают место
источнику, набору и читаемому хвосту, вместо того чтобы срезать хвост в
отпечаток почти всегда.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_observation_code_width"
down_revision = "0009_paper_trades"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "research_observations",
        "observation_type",
        existing_type=sa.String(length=48),
        type_=sa.String(length=96),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Сузить нельзя молча: уже записанные коды длиннее сорока восьми
    # символов существуют, и обрезка превратила бы разные ряды наблюдений в
    # один. Строки, которые не влезают, удаляются явно.
    op.execute(
        "DELETE FROM research_observations WHERE length(observation_type) > 48"
    )
    op.alter_column(
        "research_observations",
        "observation_type",
        existing_type=sa.String(length=96),
        type_=sa.String(length=48),
        existing_nullable=False,
    )
