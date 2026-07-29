"""Общие типы таблиц.

Два правила, которые здесь закреплены и дальше не обсуждаются:

* **Все времена — UTC** (engine-ТЗ §4.4). Колонки объявлены с timezone, а
  значения по умолчанию берутся из ``now()`` базы, а не из процесса: часы
  приложения и часы базы расходятся, и в торговом журнале это стоит дорого.
* **Все деньги и цены — NUMERIC** (UX-ТЗ §17.1). ``float`` в расчёте риска
  даёт хвосты вида ``0.30000000000000004``, а размер позиции считается
  делением на разницу цен.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from sqlalchemy import DateTime, MetaData, Numeric, String, TypeDecorator, func, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Явные имена ограничений: без них alembic генерирует случайные и миграции
# перестают быть воспроизводимыми.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class StrEnumColumn(TypeDecorator):
    """Строковая колонка, возвращающая настоящий член перечисления.

    Без неё SQLAlchemy отдаёт из базы обычную строку, и проверка вида
    ``instrument.venue is Venue.MOEX`` **всегда ложна**. Это не academic:
    именно на ней фьючерсы MOEX уходили загружаться на криптобиржу, а
    обращение к ``.value`` у строки роняло конвейер и инструмент молча
    выпадал из скана.

    Тип решает это в одном месте и навсегда: в базе по-прежнему VARCHAR
    (миграция не нужна, значения читаются глазами), в Python — член
    перечисления, на котором работают и ``is``, и ``.value``.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_cls, length: int = 32, **kwargs):
        self.enum_cls = enum_cls
        super().__init__(length=length, **kwargs)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(getattr(value, "value", value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return self.enum_cls(value)
        except ValueError:
            # Значение из базы, которого нет в перечислении, — это данные
            # старше кода. Отдаём как есть: падать на чтении журнала хуже,
            # чем увидеть незнакомую строку.
            return value


# Цена: 28 значащих, 12 после точки. Хватает и рублёвому фьючерсу, и
# сатоши-шагу криптопары.
Price = Annotated[Decimal, mapped_column(Numeric(28, 12))]
# Деньги: два знака хватило бы рублю, но комиссии и фандинг дробнее.
Money = Annotated[Decimal, mapped_column(Numeric(24, 8))]
# Доля: 0.0075 риска на сделку, 0.618 фибо — шесть знаков с запасом.
Ratio = Annotated[Decimal, mapped_column(Numeric(12, 8))]
# Объём: контракты целые, крипта дробная.
Quantity = Annotated[Decimal, mapped_column(Numeric(28, 12))]

Timestamp = Annotated[datetime, mapped_column(DateTime(timezone=True))]


def utcnow_column() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class UuidPk:
    """Первичный ключ — UUID, выданный базой.

    Идея может быть создана несколькими воркерами одновременно; автоинкремент
    в такой схеме становится точкой сериализации, а угадываемый id ещё и
    подсказывает наружу, сколько сигналов система вообще произвела.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class Versioned:
    """Версии, при которых получен результат.

    Engine-ТЗ §18 и §27: у каждой идеи и каждого прогона должны быть отпечаток
    конфигурации и версии кода. Без этого нельзя ни воспроизвести результат,
    ни понять, почему вчера и сегодня движок ответил по-разному.
    """

    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(32), nullable=False)
