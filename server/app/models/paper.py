"""Бумажная сделка на сервере (engine-ТЗ §18, §21).

Зачем это переехало сюда. Сопровождение жило только на устройстве и брало
свечи только у биржи напрямую. Bybit отвечает телефону владельца
``403 — CloudFront блокирует доступ из вашей страны``; свечей нет, сверка
не вызывается ни разу, и позиция замирает навсегда: ни тейки, ни стоп, ни
срок не проверяются. Карточка при этом выглядит живой — оба перехвата
ошибок были немыми. Именно так BTCUSDT провисел несколько дней на «взято
тейков: 2 из 3».

Сервер до биржи доходит, работает круглосуточно и не зависит от того,
открыто ли приложение. Поэтому источник истины по сделке — здесь, а
устройство показывает.

Про поля стопа. Их два, и это не дублирование. ``initial_stop`` — то, что
было обещано в плане, и он неизменен: по нему считается R и по нему потом
спорят. ``current_stop`` — то, что защищает позицию сейчас, и он двигается:
в безубыток после первой цели, к первой цели после второй. Раньше поля
было одно и неизменяемое, а факт переноса жил во временной переменной
внутри пересчёта — то есть не существовал ни в базе, ни на экране.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    Base,
    Price,
    StrEnumColumn,
    Timestamp,
    UuidPk,
    utcnow_column,
)
from .enums import Direction, PaperStatus
from .research import Json


class PaperTrade(UuidPk, Base):
    """Бумажная сделка: план, который система ведёт за владельца."""

    __tablename__ = "paper_trades"

    # Идея, из которой сделка родилась. Обязательна: сделка без идеи —
    # сирота, которую нечем объяснить, и владелец такую уже видел.
    idea_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("trade_ideas.id"), nullable=False
    )
    instrument_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("instruments.instrument_id"), nullable=False
    )
    direction: Mapped[Direction] = mapped_column(
        StrEnumColumn(Direction, 8), nullable=False
    )
    status: Mapped[PaperStatus] = mapped_column(
        StrEnumColumn(PaperStatus, 16), nullable=False
    )

    # ── План, каким он был обещан ────────────────────────────────────────
    entry: Mapped[Price] = mapped_column(nullable=False)
    initial_stop: Mapped[Price] = mapped_column(nullable=False)
    #: Цели и доли выхода. Списком, а не тремя полями: число целей —
    #: свойство стратегии, и вшивать тройку значит запретить остальные.
    tp_prices: Mapped[list] = mapped_column(Json, nullable=False, default=list)
    tp_shares: Mapped[list] = mapped_column(Json, nullable=False, default=list)

    # ── Что происходит сейчас ────────────────────────────────────────────
    current_stop: Mapped[Price] = mapped_column(nullable=False)
    tps_taken: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Когда стоп встал в безубыток. Время, а не флаг: «после какой цели»
    #: и «когда именно» — разные вопросы, и второй нужен для разбора.
    breakeven_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    realized_r: Mapped[Price] = mapped_column(nullable=False, default=0)

    opened_at: Mapped[Timestamp] = mapped_column(nullable=False)
    #: До какого момента план вообще имеет смысл. Календарный срок, а не
    #: счётчик баров: сделка на инструменте, по которому бары перестали
    #: приходить, обязана протухнуть, а не жить вечно.
    expires_at: Mapped[Timestamp] = mapped_column(nullable=False)
    #: Когда сверка последний раз действительно смотрела бары. Отдельно от
    #: `updated_at`: молчание источника обязано быть видно, иначе замершая
    #: позиция выглядит живой.
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    close_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        # Одна живая сделка на идею. Не на инструмент: дедуп по символу
        # означал, что зависшая сделка блокирует запись всех последующих
        # идей по тому же инструменту — ровно это и случилось с BTC.
        Index(
            "uq_paper_live_per_idea",
            "idea_id",
            unique=True,
            postgresql_where=("status IN ('PENDING','OPEN')"),
        ),
        Index("ix_paper_status", "status", "opened_at"),
        CheckConstraint("tps_taken >= 0", name="tps_taken_non_negative"),
    )

    @property
    def live(self) -> bool:
        return self.status in (PaperStatus.PENDING, PaperStatus.OPEN)


__all__ = ["PaperTrade"]
