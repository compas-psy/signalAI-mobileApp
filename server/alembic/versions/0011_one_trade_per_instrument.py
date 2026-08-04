"""Одна живая бумажная сделка на инструмент (engine-ТЗ §21).

Владелец прислал экран «Идеи → В работе» со словом «треш»: десять
бумажных позиций по HYPEUSDT подряд, все LONG, входы 52,2860 / 52,2840 /
52,2820 / 52,2830 / 52,2830 / 52,2950 / 52,2950 / 52,2910 / 52,2910 /
52,2950 — одна и та же сделка, пересчитанная десять раз.

Причин было две, и обе сняты в коде: скан рождал новую идею при каждом
прогоне (каждые пятнадцать минут), а дедуп сделок шёл по идее, а не по
инструменту. Здесь снимается след, который они оставили в базе, и
ставится ограничение, при котором повториться это не может.

Порядок важен: сначала схлопнуть уже наплодившееся, потом менять индекс.
Наоборот его создание упало бы на существующих данных — а миграция,
падающая на боевой базе, хуже отсутствующей.

Что считается «первой». Самая ранняя по ``opened_at``: сделка начинается
там, где родился план, и именно её владельцу показали раньше остальных.
Прочие получают собственную причину закрытия — «дубль по инструменту» и
«отменено по сроку» в статистике сливаться не должны.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_one_trade_per_instrument"
down_revision = "0010_observation_code_width"
branch_labels = None
depends_on = None

LIVE = "status IN ('PENDING','OPEN')"


def upgrade() -> None:
    # Схлопывание живёт в `app.paper.dedup`, а не здесь: SQL, спрятанный в
    # миграции, проверить нечем — а он трогает боевые данные и обязан быть
    # доказан тестом.
    from app.paper.dedup import collapse_duplicates

    collapse_duplicates(op.get_bind())

    op.drop_index("uq_paper_live_per_idea", table_name="paper_trades")
    op.create_index(
        "uq_paper_live_per_instrument",
        "paper_trades",
        ["instrument_id"],
        unique=True,
        postgresql_where=sa.text(LIVE),
    )


def downgrade() -> None:
    # Обратно — только ограничение. Схлопнутые сделки не воскрешаются:
    # «была отменена как дубль» — это факт, а не состояние индекса.
    op.drop_index("uq_paper_live_per_instrument", table_name="paper_trades")
    op.create_index(
        "uq_paper_live_per_idea",
        "paper_trades",
        ["idea_id"],
        unique=True,
        postgresql_where=sa.text(LIVE),
    )
