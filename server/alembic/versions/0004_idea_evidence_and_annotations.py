"""Доказательства и разметка в снимке идеи (§9.1, §10.6).

Детекторы выдавали метки с самого начала, но сохранять их было некуда: идея
уезжала в приложение с оценкой и планом, а разметка терялась по дороге.
Отсюда и разрыв, который владелец видел на экране, — график рисовал
собственные уровни, потому что находки детекторов до него не доходили.

Хранится **в снимке идеи**, а не пересчитывается при открытии карточки.
Пересчёт на свежих барах нарисовал бы не ту разметку, по которой принималось
решение: разбор сделки перестал бы соответствовать сделке, а именно ради
разбора всё это и делается (§12).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_idea_evidence_annotations"
down_revision = "0003_drop_shifted_moex_bars"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "trade_ideas",
        sa.Column(
            "evidence_json", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default="[]",
        ),
    )
    op.add_column(
        "trade_ideas",
        sa.Column(
            "annotations_json", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("trade_ideas", "annotations_json")
    op.drop_column("trade_ideas", "evidence_json")
