"""Журнал только дополняется: запрет UPDATE и DELETE на уровне базы.

Revision ID: 0002_append_only
Revises: 138d85fbc42e
Create Date: 2026-07-29

Engine-ТЗ §18: «Каждый переход — immutable event… Исходную идею нельзя
перезаписывать». UX-ТЗ §23: audit log append-only.

Соглашение в коде такую гарантию не даёт: достаточно одной строки в другом
модуле, одной миграции данных или одного `psql` руками, и история перестаёт
быть историей. Триггер базы отказывает всем, включая владельца приложения, —
и именно поэтому на журнал можно опираться, разбирая убыток.

Вставка разрешена. Исправление ошибочной записи делается новой записью, как
в бухгалтерии, а не правкой старой.

Под защиту попадают ровно две таблицы — те, что являются журналом:
``idea_events`` (переходы состояний) и ``audit_events`` (действия с деньгами
и лимитами). ``idea_outcomes`` намеренно оставлен изменяемым: исход по
тройному барьеру пересчитывается, когда приходят данные младшего таймфрейма
и неоднозначный бар (§15.2) удаётся разрешить. Запретить его правку значило
бы навсегда зафиксировать заведомо худшую оценку.
"""
from __future__ import annotations

from alembic import op

revision = "0002_append_only"
down_revision = "138d85fbc42e"
branch_labels = None
depends_on = None

PROTECTED = ("idea_events", "audit_events")


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION signalai_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'таблица % только дополняется: % запрещён (engine-ТЗ §18)',
                TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in PROTECTED:
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION signalai_append_only();
            """
        )


def downgrade() -> None:
    for table in PROTECTED:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table};")
    op.execute("DROP FUNCTION IF EXISTS signalai_append_only();")
