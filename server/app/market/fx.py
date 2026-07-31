"""Курс котировочной валюты к рублю (engine-ТЗ §17.1).

Зачем это вообще нужно. Размер позиции считается делением рублёвого бюджета
риска на риск одной единицы инструмента. У фьючерса MOEX риск на единицу
сразу в рублях — шаг цены умножается на стоимость шага. У бессрочного
контракта на бирже крипты риск на единицу в **USDT**: разница цен и есть
деньги. Разделить рубли на USDT нельзя, а если разделить, получится число
без единиц измерения, которое выглядит как объём.

Именно это и происходило: бюджет 562 ₽ делился на 20,16 USDT и давал
«28 ETH» — позицию на два миллиона рублей при депозите в миллион. Ошибка
тихая: оба числа правдоподобны по отдельности, и на экране их ничто не
связывает.

Откуда берётся курс. Из фьючерса Si на срочном рынке MOEX: он торгуется
в рублях за 1000 долларов, ликвиден, и доска уже загружается для вселенной
§5.2 — отдельного источника данных заводить не нужно. Официальный курс ЦБ
здесь хуже: он публикуется раз в сутки и к вечернему рынку отношения не
имеет, а размер позиции считается по текущей цене.

USDT приравнивается к доллару и это записано явно, а не подразумевается:
привязка стейблкоина — допущение, и владелец видит его в карточке идеи
вместе с самим курсом и временем его снятия.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DataQualityEvent, FxRate
from . import moex

ACCOUNT_CURRENCY = "RUB"

# Валюта котировки → валюта, курс которой её измеряет. Стейблкоины сведены
# к доллару: своего рынка к рублю у них нет, а расхождение с долларом лежит
# в пределах десятых долей процента.
PROXY: dict[str, str] = {
    "USD": "USD",
    "USDT": "USD",
    "USDC": "USD",
    "EUR": "EUR",
    "CNY": "CNY",
}

# Корень фьючерса MOEX → валюта и номинал контракта. Si торгуется в рублях
# за 1000 долларов, поэтому цена делится на 1000.
FUTURES_SOURCES: tuple[tuple[str, str, Decimal], ...] = (
    ("SI", "USD", Decimal(1000)),
    ("EU", "EUR", Decimal(1000)),
    ("CNY", "CNY", Decimal(1000)),
)

# Дольше этого срока курс не считается пригодным для расчёта размера.
# Неделя — не «примерно неделя»: за майские праздники биржа стоит четыре
# торговых дня подряд, и более короткий порог обнулял бы весь крипторынок
# каждую весну.
MAX_AGE = timedelta(days=7)

# Возраст, после которого курс показывается с оговоркой, но ещё считается.
STALE_AFTER = timedelta(hours=36)


def _nearest_priced(rows: list[moex.FortsRow], root: str) -> moex.FortsRow | None:
    """Самая оборотистая серия корня, у которой есть цена.

    Не «ближняя по экспирации»: у ближней серии в день исполнения оборот
    падает до нуля, и последняя сделка в ней может быть трёхдневной
    давности. Курс берётся оттуда, где сегодня торгуют.
    """
    prefix = root.upper()
    priced = [
        row
        for row in rows
        if row.sec_id.upper().startswith(prefix)
        and moex.root_of(row.sec_id) is not None
        and str(moex.root_of(row.sec_id)).upper() == prefix
        and row.last is not None
        and row.last > 0
    ]
    if not priced:
        return None
    return max(priced, key=lambda r: r.turnover or Decimal(0))


def rates_from_board(rows: list[moex.FortsRow]) -> dict[str, tuple[Decimal, str]]:
    """Курсы к рублю из снимка доски FORTS: валюта → (курс, источник)."""
    out: dict[str, tuple[Decimal, str]] = {}
    for root, currency, nominal in FUTURES_SOURCES:
        row = _nearest_priced(rows, root)
        if row is None or row.last is None or nominal <= 0:
            continue
        rate = row.last / nominal
        if rate <= 0:
            continue
        out[currency] = (rate, f"MOEX FORTS {row.sec_id} / {nominal:.0f}")
    return out


def record_rates(
    session: Session, rows: list[moex.FortsRow], *, now: datetime | None = None
) -> list[FxRate]:
    """Сохранить курсы, снятые с доски.

    Курс перезаписывается, а не накапливается версиями: для размера позиции
    нужен текущий, а история курса — предмет отдельной задачи и отдельной
    таблицы. Момент снятия хранится рядом, иначе «курс 78,5» невозможно
    отличить от прошлогоднего.
    """
    moment = now or datetime.now(UTC)
    found = rates_from_board(rows)
    if not found:
        # Отсутствие курса обязано оставить след. Без него крипта молча
        # перестаёт торговаться, и причина «не нашли Si на доске» не видна
        # ниоткуда: ни в идее, ни в журнале качества данных.
        session.add(
            DataQualityEvent(
                source="moex",
                flag="FX_MISSING",
                detail=(
                    f"на доске FORTS ({len(rows)} строк) нет ни одной "
                    "валютной серии с ценой: курс к рублю не обновлён"
                )[:512],
            )
        )
        return []

    saved: list[FxRate] = []
    for currency, (rate, source) in sorted(found.items()):
        existing = session.execute(
            select(FxRate).where(FxRate.currency == currency)
        ).scalar_one_or_none()
        if existing is None:
            existing = FxRate(currency=currency)
            session.add(existing)
        existing.rate_rub = rate
        existing.as_of = moment
        existing.source = source[:64]
        saved.append(existing)
    return saved


def sync_fx(session: Session, *, fetch=None, now: datetime | None = None) -> list[FxRate]:
    """Обновить курсы, самостоятельно забрав доску."""
    kwargs = {"fetch": fetch} if fetch else {}
    rows, _ = moex.forts_board(**kwargs)
    return record_rates(session, rows, now=now)


def rate_row(session: Session, currency: str) -> FxRate | None:
    """Запись курса как есть — вместе с моментом снятия и источником."""
    code = (currency or ACCOUNT_CURRENCY).upper()
    if code == ACCOUNT_CURRENCY:
        return None
    proxy = PROXY.get(code)
    if proxy is None:
        return None
    return session.execute(
        select(FxRate).where(FxRate.currency == proxy)
    ).scalar_one_or_none()


def rate_to_rub(
    session: Session, currency: str, *, now: datetime | None = None
) -> Decimal | None:
    """Сколько рублей в одной единице этой валюты.

    ``None`` — курса нет или он устарел. Это не то же самое, что единица:
    подставить единицу значит посчитать доллар по рублю, а такой размер
    позиции ошибается в восемьдесят раз. Отказ громче неверного числа.
    """
    code = (currency or ACCOUNT_CURRENCY).upper()
    if code == ACCOUNT_CURRENCY:
        return Decimal(1)
    row = rate_row(session, code)
    if row is None or row.rate_rub is None or row.rate_rub <= 0:
        return None
    moment = now or datetime.now(UTC)
    as_of = row.as_of
    if as_of is not None and as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    if as_of is not None and moment - as_of > MAX_AGE:
        return None
    return row.rate_rub


def rate_note(session: Session, currency: str, *, now: datetime | None = None) -> str:
    """Подпись под курсом для карточки идеи. Пустая строка — рублёвый инструмент."""
    code = (currency or ACCOUNT_CURRENCY).upper()
    if code == ACCOUNT_CURRENCY:
        return ""
    row = rate_row(session, code)
    if row is None or row.rate_rub is None:
        return f"курс {code} к рублю неизвестен"
    moment = now or datetime.now(UTC)
    as_of = row.as_of
    if as_of is not None and as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    stamp = as_of.strftime("%d.%m %H:%M UTC") if as_of else "время неизвестно"
    proxy = PROXY.get(code, code)
    base = f"{code} по курсу {row.rate_rub:.4f} ₽ ({row.source}, {stamp})"
    if proxy != code:
        base += f"; {code} приравнен к {proxy}"
    if as_of is not None and moment - as_of > STALE_AFTER:
        base += "; курс не обновлялся больше суток"
    return base


__all__ = [
    "ACCOUNT_CURRENCY",
    "MAX_AGE",
    "PROXY",
    "rate_note",
    "rate_row",
    "rate_to_rub",
    "rates_from_board",
    "record_rates",
    "sync_fx",
]
