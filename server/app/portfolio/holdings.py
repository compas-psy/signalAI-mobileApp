"""Снимок фактических позиций инвестиционного счёта (engine-ТЗ §6.6).

Откуда он берётся и почему именно так. Токен Т-Инвестиций на чтение выдан
владельцем приложению и лежит в защищённом хранилище телефона. На сервер он
не передаётся и передаваться не должен: токен Invest API привязан к
пользователю, а не к счёту, и видит **все** счета владельца. Хранить такой
токен на VPS значило бы сложить весь его капитал в одну точку отказа ради
удобства синхронизации.

Поэтому счёт читает устройство, а сюда приезжает уже результат чтения —
позиции и их рыночная стоимость. Сервер сравнивает состав с целевым и
предлагает, что поправить; заявок по этому предложению не отправляет никто,
сделки владелец совершает сам.

Снимки не перезаписываются, а копятся по датам: ребаланс сравнивает «было» и
«стало», и без истории это сравнение не восстановить. В пределах одной даты
снимок именно заменяется — второе чтение того же дня уточняет первое, а не
удваивает позиции.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Account, Holding, Instrument
from ..models.enums import AssetClass


@dataclass(frozen=True, slots=True)
class Position:
    """Одна позиция, как её прочитало устройство."""

    symbol: str
    quantity: Decimal
    market_value: Decimal
    average_price: Decimal | None = None
    market_price: Decimal | None = None
    instrument_id: str = ""


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Что записалось и что осталось неопознанным."""

    account_id: object
    as_of: date
    stored: int
    total_value: Decimal
    unknown: list[str]

    @property
    def note(self) -> str:
        if not self.stored:
            return "в снимке нет позиций, которые удалось опознать"
        tail = (
            f"; не опознано: {', '.join(self.unknown)}" if self.unknown else ""
        )
        return f"записано позиций: {self.stored} на {self.total_value:.0f} ₽{tail}"


def _resolve(session: Session, position: Position) -> Instrument | None:
    """Найти инструмент по канонической форме или по тикеру.

    Тикер допускается потому, что устройство читает брокера, а брокер знает
    только его. Неоднозначности здесь не возникает: тикер в пределах биржи
    уникален, а бирж у инвестиционного контура одна.
    """
    if position.instrument_id:
        found = session.execute(
            select(Instrument).where(
                Instrument.instrument_id == position.instrument_id
            )
        ).scalar_one_or_none()
        if found is not None:
            return found
    return session.execute(
        select(Instrument).where(Instrument.symbol == position.symbol).limit(1)
    ).scalars().first()


def account_for(
    session: Session,
    *,
    broker: str,
    external_id: str,
    title: str = "",
    equity: Decimal | None = None,
) -> Account:
    """Найти или завести инвестиционный счёт.

    Контур проставляется ``investment`` и не спрашивается снаружи: риск-счёт
    ведётся приложением и синхронизируется другим путём. Позволить внешнему
    вызову объявить счёт риск-контуром значило бы дать ему поменять правила
    расчёта лимитов.
    """
    account = session.execute(
        select(Account).where(
            Account.broker == broker, Account.external_id == external_id
        )
    ).scalar_one_or_none()
    if account is None:
        account = Account(
            broker=broker,
            external_id=external_id,
            title=title,
            circuit="investment",
            equity=equity or Decimal(0),
        )
        session.add(account)
        session.flush()
        return account
    if title:
        account.title = title
    if equity is not None:
        account.equity = equity
    account.synced_at = datetime.now(UTC)
    return account


def store(
    session: Session,
    account: Account,
    positions: list[Position],
    *,
    as_of: date | None = None,
) -> Snapshot:
    """Записать снимок позиций на дату.

    Неопознанные бумаги не выбрасываются молча: их тикеры возвращаются в
    ответе. Молчаливый пропуск был бы хуже всего — доли посчитались бы от
    неполной суммы, и ребаланс предложил бы докупить то, что уже есть.
    """
    moment = as_of or datetime.now(UTC).date()
    session.execute(
        delete(Holding).where(
            Holding.account_id == account.id, Holding.as_of == moment
        )
    )

    stored = 0
    total = Decimal(0)
    unknown: list[str] = []
    for position in positions:
        if position.quantity == 0:
            continue
        instrument = _resolve(session, position)
        if instrument is None:
            unknown.append(position.symbol)
            continue
        session.add(
            Holding(
                account_id=account.id,
                instrument_id=instrument.instrument_id,
                as_of=moment,
                quantity=position.quantity,
                average_price=position.average_price,
                market_price=position.market_price,
                market_value=position.market_value,
                asset_class=instrument.asset_class,
                source="broker",
            )
        )
        stored += 1
        total += position.market_value
    session.flush()
    return Snapshot(
        account_id=account.id,
        as_of=moment,
        stored=stored,
        total_value=total,
        unknown=unknown,
    )


__all__ = ["AssetClass", "Position", "Snapshot", "account_for", "store"]
