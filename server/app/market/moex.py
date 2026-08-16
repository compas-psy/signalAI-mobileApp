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

INTERVALS: dict[Timeframe, int] = {
    Timeframe.M10: 10,
    Timeframe.H1: 60,
    Timeframe.D1: 24,
}


@dataclass(frozen=True, slots=True)
class FortsRow:
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


MOSCOW = timezone(timedelta(hours=3))


def _moscow_to_utc(value: str) -> datetime:
    naive = datetime.fromisoformat(value)
    if naive.tzinfo is not None:
        return naive.astimezone(UTC)
    return naive.replace(tzinfo=MOSCOW).astimezone(UTC)


def _open_time(begin: str, timeframe: Timeframe) -> datetime:
    moment = _moscow_to_utc(begin)
    if timeframe is not Timeframe.D1:
        return moment
    trading_day = datetime.fromisoformat(begin).date()
    return datetime(trading_day.year, trading_day.month, trading_day.day, tzinfo=UTC)


def _day(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _paged(url_for, block: str, *, fetch=http_json) -> tuple[list[dict], list[FetchReport]]:
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
            break
        if len(rows) < PAGE_SIZE:
            break
        start += len(rows)
    return collected, reports


def forts_board(*, fetch=http_json) -> tuple[list[FortsRow], list[FetchReport]]:
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
_SEC_MINIMAL = "SECID,SHORTNAME,MINSTEP,DECIMALS,LOTSIZE"
_MD_MINIMAL = "SECID,LAST,VALTODAY"
_ALL_COLUMNS = ""


def stock_board(
    market: str, board: str, *, fetch=http_json
) -> tuple[list[BoardRow], list[FetchReport]]:
    sec_columns = _SEC_COLUMNS.get(market)
    md_columns = _MD_COLUMNS.get(market)
    if sec_columns is None or md_columns is None:
        raise ValueError(f"рынок {market!r} не описан: неизвестен набор колонок")

    def request(sec: str, md: str):
        def url(start: int) -> str:
            columns = ""
            if sec:
                columns += f"&securities.columns={sec}"
            if md:
                columns += f"&marketdata.columns={md}"
            return (
                f"{BASE}/engines/stock/markets/{market}/boards/{board}"
                "/securities.json?iss.meta=off&iss.only=securities,marketdata"
                f"{columns}&limit={PAGE_SIZE}&start={start}"
            )

        rows, reports = _paged(url, "securities", fetch=fetch)
        data, more = _paged(url, "marketdata", fetch=fetch)
        return rows, data, [*reports, *more]

    securities, market_data, reports = request(sec_columns, md_columns)
    if not securities:
        securities, market_data, retry = request(_SEC_MINIMAL, _MD_MINIMAL)
        reports = [*reports, *retry]
    if not securities:
        securities, market_data, retry = request(_ALL_COLUMNS, _ALL_COLUMNS)
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


def security_issuer_id(
    sec_id: str, *, fetch=http_json
) -> tuple[str | None, FetchReport]:
    """Вернуть source-backed identity эмитента, не угадывая по имени/тикеру."""
    payload, report = fetch(
        f"{BASE}/securities/{sec_id}.json?iss.meta=off&iss.only=description"
    )
    for row in iss_rows(payload, "description"):
        if str(row.get("name") or "").strip().lower() != "emitent_inn":
            continue
        raw = str(row.get("value") or "").strip()
        inn = "".join(ch for ch in raw if ch.isdigit())
        if len(inn) in (10, 12):
            return f"MOEX:INN:{inn}", report
    return None, report


def dividends(sec_id: str, *, fetch=http_json) -> tuple[list[tuple[date, Decimal]], FetchReport]:
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
                open_interest=None,
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
    from dataclasses import replace

    out: list[Candle] = []
    for candle in daily:
        value = series.get(candle.open_time.date())
        if value is None:
            out.append(candle)
            continue
        flags = tuple(
            f for f in candle.quality_flags if f != QualityFlag.OI_UNAVAILABLE.value
        )
        out.append(replace(candle, open_interest=value, quality_flags=flags))
    return out


MONTH_CODES = "FGHJKMNQUVXZ"
_SHORT_CODE = re.compile(rf"^(?P<root>.+?)(?P<month>[{MONTH_CODES}])(?P<year>\d)$")


def root_of(sec_id: str) -> str | None:
    match = _SHORT_CODE.match(sec_id.strip())
    if match is None:
        return None
    root = match.group("root")
    return root or None


def series_by_root(rows: list[FortsRow]) -> dict[str, list[FortsRow]]:
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
    prefix = root.upper()
    candidates = [
        r
        for r in rows
        if r.sec_id.upper().startswith(prefix)
        and r.last_trade_date is not None
        and (r.last_trade_date - today).days > 5
        and (r.turnover or Decimal(0)) > 0
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda r: r.last_trade_date)
