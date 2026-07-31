"""Адаптер MOEX ISS (engine-ТЗ §4.1, §19.2 UX-ТЗ).

Адреса и наборы колонок взяты из клиента приложения, который работает с
биржей больше месяца. Это не экономия усилий: ISS отдаёт по сорок колонок на
инструмент, если их не перечислить, и запрос доски опционов на этом
обрывался по таймауту чтения тела. Проверенный список колонок дороже
красивого кода.

Ключи не нужны: ISS отдаёт всё перечисленное без авторизации.

Открытый интерес. На срочном рынке `OPENPOSITION` приходит в снимке доски —
это одно текущее число, а не ряд. Внутридневного ряда OI у ISS нет вовсе, и
это записано как ограничение, а не обойдено подстановкой. Дневной ряд
доступен через блок истории и запрашивается отдельно.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from ..models.enums import QualityFlag, Timeframe
from .candles import Candle
from .http import FetchReport, http_json, iss_rows

BASE = "https://iss.moex.com/iss"
FORTS = "engines/futures/markets/forts"
SHARES = "engines/stock/markets/shares/boards/TQBR"
FORTS_HISTORY = "history/engines/futures/markets/forts"

PAGE_SIZE = 100
MAX_PAGES = 40

# Интервалы свечей ISS. Четырёхчасового у биржи нет — он строится склейкой
# часовых, и подменять его молча нельзя (см. resample_hours).
INTERVALS: dict[Timeframe, int] = {
    Timeframe.M10: 10,
    Timeframe.H1: 60,
    Timeframe.D1: 24,
}


@dataclass(frozen=True, slots=True)
class FortsRow:
    """Строка доски срочного рынка."""

    sec_id: str
    short_name: str
    last: Decimal | None
    turnover: Decimal | None
    open_interest: Decimal | None
    min_step: Decimal | None
    step_price: Decimal | None
    decimals: int | None
    last_trade_date: date | None
    updated_at: str | None
    bid: Decimal | None = None
    ask: Decimal | None = None

    @property
    def relative_spread(self) -> Decimal | None:
        """Спред в долях цены — мера ликвидности §5.2.

        Это **снимок**, а не медиана за 30 дней: ISS не отдаёт историю
        котировок. Тот, кто принимает по нему решение, обязан знать разницу,
        поэтому она названа здесь, а не спрятана в имени поля.
        """
        if self.bid is None or self.ask is None or self.bid <= 0:
            return None
        mid = (self.bid + self.ask) / 2
        if mid <= 0:
            return None
        return (self.ask - self.bid) / mid


@dataclass(frozen=True, slots=True)
class ShareRow:
    sec_id: str
    short_name: str
    last: Decimal | None
    turnover: Decimal | None
    min_step: Decimal | None
    lot_size: int | None


def _dec(value: Any) -> Decimal | None:
    """Число из ответа биржи в Decimal — через строку, без float."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Биржа отдаёт время **по Москве** и без указания зоны. С 2014 года в России
# нет перехода на летнее время, поэтому смещение постоянное.
MOSCOW = timezone(timedelta(hours=3))


def _moscow_to_utc(value: str) -> datetime:
    """Время ISS в UTC.

    Раньше здесь стояло ``replace(tzinfo=UTC)`` — то есть московское время
    просто объявлялось UTC. Ряд MOEX уезжал на три часа вперёд, и на живых
    данных это выглядело как бар из будущего: свежайший бар был помечен
    19:00 UTC, когда на сервере было 16:29 UTC.

    Сдвиг ломает не только вид. Склейка 4H с якорем на открытие сессии
    попадала бы не на те свечи, «устарели ли данные» считалось бы с ошибкой в
    три часа, а крипта и срочный рынок легли бы на одну шкалу времени
    несовпадающими — то есть любое их сопоставление было бы ложным.
    """
    naive = datetime.fromisoformat(value)
    if naive.tzinfo is not None:
        return naive.astimezone(UTC)
    return naive.replace(tzinfo=MOSCOW).astimezone(UTC)


def _open_time(begin: str, timeframe: Timeframe) -> datetime:
    """Начало свечи в канонической шкале §4.4.

    Внутридневная свеча — это момент, и он переводится в UTC.

    Дневная свеча — это **торговая сессия**, а не момент. Перевод московской
    полуночи в UTC дал бы 21:00 предыдущего дня, и дневной бар за 29 июля
    оказался бы датирован 28-м: открытый интерес перестал бы находиться по
    дате, а недельные и месячные склейки поехали бы на день. Поэтому день
    остаётся днём — торговой датой в полночь UTC.
    """
    moment = _moscow_to_utc(begin)
    if timeframe is not Timeframe.D1:
        return moment
    trading_day = datetime.fromisoformat(begin).date()
    return datetime(
        trading_day.year, trading_day.month, trading_day.day, tzinfo=UTC
    )


def _day(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _paged(url_for, block: str, *, fetch=http_json) -> tuple[list[dict], list[FetchReport]]:
    """Обойти страницы ISS с защитой от игнорирования курсора.

    Дедупликация нужна не для красоты: если биржа не соблюдает `start`, цикл
    честно отработает все сорок запросов и склеит сорок копий одной страницы.

    Ключ — **строка целиком**, а не её первая колонка. Первая колонка годится
    для доски, где это `SECID`, и губительна для свечей, где это `open`: три
    бара с одинаковой ценой открытия схлопнулись бы в один, и ряд молча
    поредел бы. Одинаковые строки целиком — это и есть повтор страницы.
    """
    collected: list[dict] = []
    reports: list[FetchReport] = []
    seen: set[tuple] = set()
    start = 0
    for _ in range(MAX_PAGES):
        payload, report = fetch(url_for(start))
        reports.append(report)
        rows = iss_rows(payload, block)
        if not rows:
            break
        fresh = 0
        for row in rows:
            key = tuple(sorted((str(k), str(v)) for k, v in row.items()))
            if key in seen:
                continue
            seen.add(key)
            collected.append(row)
            fresh += 1
        if fresh == 0:
            break            # биржа отдаёт ту же страницу — курсор не работает
        if len(rows) < PAGE_SIZE:
            break            # неполная страница последняя, лишний запрос не нужен
        start += len(rows)
    return collected, reports


def forts_board(*, fetch=http_json) -> tuple[list[FortsRow], list[FetchReport]]:
    """Снимок доски FORTS вместе с открытым интересом."""

    def url(start: int) -> str:
        return (
            f"{BASE}/{FORTS}/securities.json"
            "?iss.meta=off&iss.only=securities,marketdata"
            "&securities.columns=SECID,SHORTNAME,LASTTRADEDATE,MINSTEP,STEPPRICE,DECIMALS"
            "&marketdata.columns=SECID,LAST,VALTODAY,OPENPOSITION,UPDATETIME,BID,OFFER"
            f"&limit={PAGE_SIZE}&start={start}"
        )

    securities, r1 = _paged(url, "securities", fetch=fetch)
    market, r2 = _paged(url, "marketdata", fetch=fetch)
    by_id = {row.get("SECID"): row for row in market}

    rows = []
    for sec in securities:
        sec_id = sec.get("SECID")
        if not sec_id:
            continue
        md = by_id.get(sec_id, {})
        rows.append(
            FortsRow(
                sec_id=str(sec_id),
                short_name=str(sec.get("SHORTNAME") or ""),
                last=_dec(md.get("LAST")),
                turnover=_dec(md.get("VALTODAY")),
                open_interest=_dec(md.get("OPENPOSITION")),
                min_step=_dec(sec.get("MINSTEP")),
                step_price=_dec(sec.get("STEPPRICE")),
                decimals=_int(sec.get("DECIMALS")),
                last_trade_date=_day(sec.get("LASTTRADEDATE")),
                updated_at=md.get("UPDATETIME"),
                bid=_dec(md.get("BID")),
                ask=_dec(md.get("OFFER")),
            )
        )
    return rows, [*r1, *r2]


def shares_board(*, fetch=http_json) -> tuple[list[ShareRow], list[FetchReport]]:
    """Снимок доски TQBR. Лот здесь настоящий и нужен для размера позиции."""

    def url(start: int) -> str:
        return (
            f"{BASE}/{SHARES}/securities.json"
            "?iss.meta=off&iss.only=securities,marketdata"
            "&securities.columns=SECID,SHORTNAME,MINSTEP,DECIMALS,LOTSIZE"
            "&marketdata.columns=SECID,LAST,VALTODAY"
            f"&limit={PAGE_SIZE}&start={start}"
        )

    securities, r1 = _paged(url, "securities", fetch=fetch)
    market, r2 = _paged(url, "marketdata", fetch=fetch)
    by_id = {row.get("SECID"): row for row in market}

    rows = []
    for sec in securities:
        sec_id = sec.get("SECID")
        if not sec_id:
            continue
        md = by_id.get(sec_id, {})
        rows.append(
            ShareRow(
                sec_id=str(sec_id),
                short_name=str(sec.get("SHORTNAME") or ""),
                last=_dec(md.get("LAST")),
                turnover=_dec(md.get("VALTODAY")),
                min_step=_dec(sec.get("MINSTEP")),
                lot_size=_int(sec.get("LOTSIZE")),
            )
        )
    return rows, [*r1, *r2]


@dataclass(frozen=True, slots=True)
class BoardRow:
    """Строка любой доски фондового рынка: акции, фонды, облигации.

    ``extra`` держит колонки, специфичные для рынка: доходность и дюрацию у
    облигаций, размер выпуска у акций. Заводить под каждый рынок свой класс
    незачем — различие ровно в наборе чисел, а не в устройстве строки.
    """

    sec_id: str
    short_name: str
    board: str
    last: Decimal | None
    turnover: Decimal | None
    min_step: Decimal | None
    lot_size: int | None
    decimals: int | None
    extra: dict[str, Any]

    def number(self, key: str) -> Decimal | None:
        return _dec(self.extra.get(key))


# Наборы колонок по рынкам. Перечислять обязательно: без `columns` ISS
# отдаёт по сорок полей на бумагу, и запрос доски рвётся по таймауту.
_SEC_COLUMNS = {
    "shares": "SECID,SHORTNAME,MINSTEP,DECIMALS,LOTSIZE,ISSUESIZE,SECTYPE",
    "bonds": (
        "SECID,SHORTNAME,MINSTEP,DECIMALS,LOTSIZE,MATDATE,COUPONPERCENT,"
        "COUPONVALUE,FACEVALUE,SECTYPE"
    ),
}
_MD_COLUMNS = {
    "shares": "SECID,LAST,VALTODAY",
    "bonds": "SECID,LAST,VALTODAY,YIELD,DURATION",
}

# Набор колонок, который есть на любой доске рынка.
#
# Перечень колонок обязателен: без него ISS отдаёт по сорок полей на бумагу.
# Но у него есть цена — если запросить колонку, которой на **этой** доске
# нет, биржа не ругается, а возвращает блок пустым. Именно так пропала вся
# доска фондов TQTF: она живёт на рынке shares, где у акций есть ISSUESIZE,
# а у паёв фонда — нет. Ответ приходил с нулём строк, класс активов исчезал
# целиком, и снаружи это выглядело как «фондов на бирже не нашлось».
_SEC_MINIMAL = "SECID,SHORTNAME,MINSTEP,DECIMALS,LOTSIZE"
_MD_MINIMAL = "SECID,LAST,VALTODAY"


def stock_board(
    market: str, board: str, *, fetch=http_json
) -> tuple[list[BoardRow], list[FetchReport]]:
    """Снимок доски фондового рынка (акции TQBR, фонды TQTF, ОФЗ TQOB).

    Один вход на все три рынка. Инвестиционный контур отличается от срочного
    не способом получения данных, а тем, какие числа у бумаги есть: у
    облигации — доходность и дюрация, у акции — размер выпуска. Оба набора
    приезжают одним запросом и складываются в ``extra``.
    """
    sec_columns = _SEC_COLUMNS.get(market)
    md_columns = _MD_COLUMNS.get(market)
    if sec_columns is None or md_columns is None:
        raise ValueError(f"рынок {market!r} не описан: неизвестен набор колонок")

    def request(sec: str, md: str):
        def url(start: int) -> str:
            return (
                f"{BASE}/engines/stock/markets/{market}/boards/{board}"
                "/securities.json?iss.meta=off&iss.only=securities,marketdata"
                f"&securities.columns={sec}"
                f"&marketdata.columns={md}"
                f"&limit={PAGE_SIZE}&start={start}"
            )

        rows, reports = _paged(url, "securities", fetch=fetch)
        data, more = _paged(url, "marketdata", fetch=fetch)
        return rows, data, [*reports, *more]

    securities, market_data, reports = request(sec_columns, md_columns)
    if not securities:
        # Пусто может значить и «доска закрыта», и «одной из колонок на этой
        # доске нет». Второе неотличимо от первого по ответу, поэтому
        # спрашиваем ещё раз тем набором, который есть везде. Дополнительные
        # поля при этом теряются — но бумага без капитализации полезнее, чем
        # отсутствующий класс активов.
        securities, market_data, retry = request(_SEC_MINIMAL, _MD_MINIMAL)
        reports = [*reports, *retry]
    by_id = {row.get("SECID"): row for row in market_data}

    rows: list[BoardRow] = []
    for sec in securities:
        sec_id = sec.get("SECID")
        if not sec_id:
            continue
        md = by_id.get(sec_id, {})
        extra = {k: v for k, v in {**sec, **md}.items() if k != "SECID"}
        rows.append(
            BoardRow(
                sec_id=str(sec_id),
                short_name=str(sec.get("SHORTNAME") or ""),
                board=board,
                last=_dec(md.get("LAST")),
                turnover=_dec(md.get("VALTODAY")),
                min_step=_dec(sec.get("MINSTEP")),
                lot_size=_int(sec.get("LOTSIZE")),
                decimals=_int(sec.get("DECIMALS")),
                extra=extra,
            )
        )
    return rows, reports


def dividends(sec_id: str, *, fetch=http_json) -> tuple[list[tuple[date, Decimal]], FetchReport]:
    """История дивидендов бумаги.

    Единственный фундаментальный ряд, который ISS отдаёт без ключей и без
    оговорок. Он и берётся: дивидендная доходность считается по фактическим
    выплатам за последние 12 месяцев, а не по обещаниям аналитиков.
    """
    payload, report = fetch(
        f"{BASE}/securities/{sec_id}/dividends.json?iss.meta=off&iss.only=dividends"
    )
    result: list[tuple[date, Decimal]] = []
    for row in iss_rows(payload, "dividends"):
        day = _day(row.get("registryclosedate"))
        value = _dec(row.get("value"))
        if day is None or value is None or value <= 0:
            continue
        result.append((day, value))
    result.sort(key=lambda item: item[0])
    return result, report


def candles(
    sec_id: str,
    timeframe: Timeframe,
    since: date,
    *,
    path: str = FORTS,
    fetch=http_json,
) -> tuple[list[Candle], list[FetchReport]]:
    """Свечи инструмента.

    Четырёхчасовой таймфрейм у ISS отсутствует, и запрос его отклоняется
    исключением, а не подменяется часовым: молчаливая подмена таймфрейма —
    худший вид неверных данных, потому что результат выглядит правдоподобно.
    """
    if timeframe not in INTERVALS:
        raise ValueError(
            f"MOEX ISS не отдаёт таймфрейм {timeframe.value}. "
            "Доступны: 10m, 1h, 1d; 4H строится склейкой часовых."
        )
    interval = INTERVALS[timeframe]

    def url(start: int) -> str:
        return (
            f"{BASE}/{path}/securities/{sec_id}/candles.json"
            "?iss.meta=off&iss.only=candles"
            f"&interval={interval}&from={since.isoformat()}&start={start}"
        )

    rows, reports = _paged(url, "candles", fetch=fetch)
    result: list[Candle] = []
    for row in rows:
        begin = row.get("begin")
        close = _dec(row.get("close"))
        if not isinstance(begin, str) or close is None:
            # Свеча без времени или без цены бесполезна. Подставлять «сейчас»
            # вместо неразобранной даты нельзя — сдвиг ряда испортит всё,
            # что по нему считается.
            continue
        try:
            open_time = _open_time(begin, timeframe)
        except ValueError:
            continue
        result.append(
            Candle(
                open_time=open_time,
                open=_dec(row.get("open")) or close,
                high=_dec(row.get("high")) or close,
                low=_dec(row.get("low")) or close,
                close=close,
                volume_units=_dec(row.get("volume")),
                volume_notional=_dec(row.get("value")),
                open_interest=None,     # ISS не отдаёт OI в свечах — см. модуль
                is_closed=True,
                source="moex",
                quality_flags=(QualityFlag.OI_UNAVAILABLE.value,),
            )
        )
    result.sort(key=lambda c: c.open_time)
    return result, reports


def daily_open_interest(
    sec_id: str, since: date, *, fetch=http_json
) -> tuple[dict[date, Decimal], list[FetchReport]]:
    """Дневной ряд открытого интереса из блока истории.

    Единственный способ получить у ISS не одно число, а ряд. Внутри дня OI
    по-прежнему недоступен, и фактор «Volume and OI» на часовом таймфрейме
    остаётся неизмеренным — честно неизмеренным, а не нулевым.
    """

    def url(start: int) -> str:
        return (
            f"{BASE}/{FORTS_HISTORY}/securities/{sec_id}.json"
            "?iss.meta=off&iss.only=history"
            "&history.columns=TRADEDATE,OPENPOSITION"
            f"&from={since.isoformat()}&start={start}"
        )

    rows, reports = _paged(url, "history", fetch=fetch)
    series: dict[date, Decimal] = {}
    for row in rows:
        day = _day(row.get("TRADEDATE"))
        value = _dec(row.get("OPENPOSITION"))
        if day is not None and value is not None:
            series[day] = value
    return series, reports


def attach_open_interest(
    daily: list[Candle], series: dict[date, Decimal]
) -> list[Candle]:
    """Приклеить дневной OI к дневным свечам.

    Свеча, для которой OI не нашёлся, остаётся без него и сохраняет флаг —
    не «берём вчерашний»: перенос значения через дыру создаёт ряд, которого
    не было.
    """
    from dataclasses import replace

    out: list[Candle] = []
    for candle in daily:
        value = series.get(candle.open_time.date())
        if value is None:
            out.append(candle)
            continue
        flags = tuple(f for f in candle.quality_flags if f != QualityFlag.OI_UNAVAILABLE.value)
        out.append(replace(candle, open_interest=value, quality_flags=flags))
    return out


# Месяцы поставки по стандарту фьючерсных кодов. Год — одна цифра, поэтому
# `SiU6` читается как «Si, сентябрь, ...6 год».
MONTH_CODES = "FGHJKMNQUVXZ"
_SHORT_CODE = re.compile(rf"^(?P<root>.+?)(?P<month>[{MONTH_CODES}])(?P<year>\d)$")


def root_of(sec_id: str) -> str | None:
    """Корень контракта из короткого кода биржи: ``SiU6`` → ``Si``.

    Разбор идёт с конца, а не по «первым буквам»: у ``SiU6`` первые буквы —
    ``SiU``, и наивная эвристика склеила бы корень с месяцем поставки. Тогда
    каждая серия выглядела бы отдельным корнем, а роллирование — невозможным.
    """
    match = _SHORT_CODE.match(sec_id.strip())
    if match is None:
        return None
    root = match.group("root")
    return root or None


def series_by_root(rows: list[FortsRow]) -> dict[str, list[FortsRow]]:
    """Разложить доску по корням, ближняя серия первой.

    Серии без даты исполнения отбрасываются: без неё нельзя ни выбрать
    ближнюю, ни проверить срок до экспирации §5.2.
    """
    grouped: dict[str, list[FortsRow]] = {}
    for row in rows:
        root = root_of(row.sec_id)
        if root is None or row.last_trade_date is None:
            continue
        grouped.setdefault(root.upper(), []).append(row)
    for series in grouped.values():
        series.sort(key=lambda r: r.last_trade_date)  # type: ignore[arg-type,return-value]
    return grouped


def nearest_series(rows: list[FortsRow], root: str, today: date) -> FortsRow | None:
    """Ближняя ликвидная серия корня.

    Серия, до исполнения которой меньше порога §5.2, не берётся: держать
    позицию в контракте, который вот-вот истечёт, — отдельный риск, не
    имеющий отношения к сетапу.
    """
    # Регистр важен: у биржи secid ближнего фьючерса на доллар — `SiU6`, а
    # корень в конфигурации записан `SI`. Прямое сравнение молча оставляло
    # вселенную пустой, и планировщик работал вхолостую, не жалуясь.
    prefix = root.upper()
    candidates = [
        r for r in rows
        if r.sec_id.upper().startswith(prefix)
        and r.last_trade_date is not None
        and (r.last_trade_date - today).days > 5
        and (r.turnover or Decimal(0)) > 0
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda r: r.last_trade_date)
