"""Бумажный журнал на сервере (engine-ТЗ §18, §21).

Сопровождение сделки жило только на устройстве и брало свечи только у
биржи напрямую. Bybit отвечает телефону владельца ``403 — CloudFront
блокирует доступ из вашей страны``: свечей нет, сверка не вызывается ни
разу, позиция замирает навсегда. Ни тейки, ни стоп, ни срок не
проверяются, а карточка выглядит живой — перехваты ошибок были немыми.
Так BTCUSDT провисел несколько дней на «взято тейков: 2 из 3».

Сервер до биржи доходит и работает, когда телефон выключен. Поэтому
источник истины по сделке переезжает сюда.

Два поля стопа — не дублирование. ``initial_stop`` неизменен: по нему
считается R и по нему потом спорят. ``current_stop`` двигается — в
безубыток после первой цели, к первой цели после второй. Раньше поле было
одно и неизменяемое, а факт переноса жил во временной переменной внутри
пересчёта, то есть не существовал ни в базе, ни на экране.

Частичный уникальный индекс — по идее, а не по инструменту. Дедуп по
символу означал, что зависшая сделка блокирует запись всех последующих
идей по тому же инструменту; владелец увидел это как «в журнале не все
идеи».
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_paper_trades"
down_revision = "0008_research_early_signals"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "paper_trades",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("idea_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("entry", sa.Numeric(28, 12), nullable=False),
        sa.Column("initial_stop", sa.Numeric(28, 12), nullable=False),
        sa.Column("tp_prices", JSONB, nullable=False, server_default="[]"),
        sa.Column("tp_shares", JSONB, nullable=False, server_default="[]"),
        sa.Column("current_stop", sa.Numeric(28, 12), nullable=False),
        sa.Column("tps_taken", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("breakeven_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("realized_r", sa.Numeric(28, 12), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=False, server_default=""),
        sa.Column(
            "close_reason", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["idea_id"], ["trade_ideas.id"]),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.instrument_id"]),
        sa.CheckConstraint("tps_taken >= 0", name="tps_taken_non_negative"),
    )
    op.create_index(
        "uq_paper_live_per_idea",
        "paper_trades",
        ["idea_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING','OPEN')"),
    )
    op.create_index("ix_paper_status", "paper_trades", ["status", "opened_at"])


def downgrade() -> None:
    op.drop_index("ix_paper_status", table_name="paper_trades")
    op.drop_index("uq_paper_live_per_idea", table_name="paper_trades")
    op.drop_table("paper_trades")
