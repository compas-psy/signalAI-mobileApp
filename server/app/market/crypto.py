"""Адаптер криптобиржи (engine-ТЗ §4.2, §13; UX-ТЗ §19.3).

Публичные эндпоинты Bybit v5: свечи, тикеры, открытый интерес, фандинг.
Ключей они не требуют — и здесь их быть не должно. Ключи живут в контейнере
исполнения, который к рыночным данным отношения не имеет (§19.1): сервис,
читающий котировки, не обязан уметь отправлять заявки.

Открытый интерес у крипты, в отличие от MOEX, доступен рядом — и это
единственный рынок, где фактор «Volume and OI» измеряется полноценно.
Хранится он в **номинале** (§13), а не в контрактах: число контрактов
несопоставимо между инструментами и во времени.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from ..models.enums import Timeframe
from .candles import Candle
from .http import FetchReport, http_json

BASE = "https://api.bybit.com"

# Интервалы Bybit. Значения — строки, как их ждёт биржа.
INTERVALS: dict[Timeframe, str] = {
    Timeframe.M15: "15",
    Timeframe.H1: "60",
    Timeframe.H4: "240",
    Timeframe.D1: "D",
}

_TIMEFRAME_DURATION: dict[Timeframe, timedelta] = {
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
}


@dataclass(frozen=True, slots=True)
class Ticker:
    symbol: str
    last: Decimal | None
    turnover_24h: Decimal | None
    open_interest: Decimal | None
    funding_rate: Decimal | None
    next_funding_time: datetime | None
    bid: Decimal | None
    ask: Decimal | None

    @property
    def relative_spread(self) -> float | None:
        """Спред в долях цены — то, чем меряется ликвидность §9.3."""
        if self.bid is None or self.ask is None or self.bid <= 0:
            return None
        mid = (self.bid + self.ask) / 2
        if mid <= 0:
            return None
        return float((self.ask - self.bid) / mid)


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _ms(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _result(payload: Any) -> dict:
    """Развернуть конверт ответа Bybit, проверив код возврата.

    Биржа отвечает 200 и кладёт ошибку внутрь тела. Без этой проверки отказ
    выглядел бы как пустой список — то есть как «рынка нет».
    """
    if not isinstance(payload, dict):
        raise ValueError("ответ Bybit не является объектом")
    code = payload.get("retCode")
    if code not in (0, None):
        raise ValueError(f"Bybit вернул ошибку {code}: {payload.get('retMsg')}")
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def tickers(
    *, category: str = "linear", fetch=http_json
) -> tuple[list[Ticker], FetchReport]:
    """Снимок рынка: цена, оборот, OI, фандинг, лучшие котировки."""
    url = f"{BASE}/v5/market/tickers?category={category}"
    payload, report = fetch(url)
    rows = _result(payload).get("list") or []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            Ticker(
                symbol=str(row.get("symbol") or ""),
                last=_dec(row.get("lastPrice")),
                turnover_24h=_dec(row.get("turnover24h")),
                open_interest=_dec(row.get("openInterestValue"))
                or _dec(row.get("openInterest")),
                funding_rate=_dec(row.get("fundingRate")),
                next_funding_time=_ms(row.get("nextFundingTime")),
                bid=_dec(row.get("bid1Price")),
                ask=_dec(row.get("ask1Price")),
            )
        )
    return out, report


@dataclass(frozen=True, slots=True)
class InstrumentInfo:
    """Спецификация контракта: шаг цены, шаг объёма, минимальный лот.

    Эти три числа входят в формулу размера позиции §17.1. Значение «1 по
    умолчанию» здесь недопустимо: оно даёт правдоподобный, но неверный
    объём — а это уже деньги.
    """

    symbol: str
    status: str
    contract_type: str
    base_coin: str
    quote_coin: str
    tick_size: Decimal | None
    qty_step: Decimal | None
    min_order_qty: Decimal | None
    launch_time: datetime | None
    delivery_time: datetime | None

    @property
    def is_perpetual(self) -> bool:
        return "Perpetual" in self.contract_type

    @property
    def is_trading(self) -> bool:
        return self.status == "Trading"


def instruments_info(
    *, category: str = "linear", fetch=http_json
) -> tuple[list[InstrumentInfo], FetchReport]:
    """Справочник контрактов.

    Нужен для §5.3 (delisting и pre-market отсеиваются по ``status``) и для
    §17.1 (шаг объёма и минимальный лот). ``deliveryTime`` отличает вечный
    контракт от поставочного: у вечного оно нулевое.
    """
    url = f"{BASE}/v5/market/instruments-info?category={category}&limit=1000"
    payload, report = fetch(url)
    rows = _result(payload).get("list") or []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        lot = row.get("lotSizeFilter") or {}
        price = row.get("priceFilter") or {}
        delivery = _ms(row.get("deliveryTime"))
        out.append(
            InstrumentInfo(
                symbol=str(row.get("symbol") or ""),
                status=str(row.get("status") or ""),
                contract_type=str(row.get("contractType") or ""),
                base_coin=str(row.get("baseCoin") or ""),
                quote_coin=str(row.get("quoteCoin") or ""),
                tick_size=_dec(price.get("tickSize")),
                qty_step=_dec(lot.get("qtyStep")),
                min_order_qty=_dec(lot.get("minOrderQty")),
                launch_time=_ms(row.get("launchTime")),
                # Ноль — «поставки нет», а не «поставка в 1970 году».
                delivery_time=delivery if str(row.get("deliveryTime")) != "0" else None,
            )
        )
    return out, report


def _parse_kline_rows(
    rows: list,
    *,
    duration: timedelta,
    moment: datetime,
    source: str,
) -> list[Candle]:
    out: list[Candle] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        open_time = _ms(row[0])
        close = _dec(row[4])
        if open_time is None or close is None:
            continue
        out.append(
            Candle(
                open_time=open_time,
                open=_dec(row[1]) or close,
                high=_dec(row[2]) or close,
                low=_dec(row[3]) or close,
                close=close,
                volume_units=_dec(row[5]),
                volume_notional=_dec(row[6]) if len(row) > 6 else None,
                # Bybit includes the currently forming candle in /kline.
                # Treating it as closed made the canonical DB repaint and,
                # more importantly, allowed the tracker to believe a move was
                # final before the interval ended.
                is_closed=open_time + duration <= moment,
                source=source,
            )
        )
    out.sort(key=lambda c: c.open_time)
    return out


def klines(
    symbol: str,
    timeframe: Timeframe,
    *,
    limit: int = 200,
    category: str = "linear",
    fetch=http_json,
    now: datetime | None = None,
) -> tuple[list[Candle], FetchReport]:
    """Свечи старших таймфреймов с честным признаком закрытия.

    Биржа отдаёт их **от новых к старым** и включает текущую формирующуюся
    свечу. Ряд разворачивается здесь, а ``is_closed`` вычисляется по времени
    интервала — ingestion сохранит только закрытые бары, в то время как UI
    может использовать текущую свечу как display-only overlay.
    """
    if timeframe not in INTERVALS:
        raise ValueError(
            f"Bybit не отдаёт таймфрейм {timeframe.value}; "
            f"доступны {sorted(t.value for t in INTERVALS)}"
        )
    url = (
        f"{BASE}/v5/market/kline?category={category}&symbol={symbol}"
        f"&interval={INTERVALS[timeframe]}&limit={limit}"
    )
    payload, report = fetch(url)
    rows = _result(payload).get("list") or []
    moment = now or datetime.now(UTC)
    return (
        _parse_kline_rows(
            rows,
            duration=_TIMEFRAME_DURATION[timeframe],
            moment=moment,
            source="bybit",
        ),
        report,
    )


def minute_klines(
    symbol: str,
    *,
    limit: int = 20,
    category: str = "linear",
    fetch=http_json,
    now: datetime | None = None,
) -> tuple[list[Candle], FetchReport]:
    """Последние минутные свечи для server-side сопровождения paper-сделок.

    Они намеренно **не пишутся** в каноническую таблицу ``bars`` и не
    участвуют в поиске сетапов. Это оперативный датчик исполнения: серверу
    нельзя ждать закрытия H1, если цена DOGE/USDT уже коснулась подтверждённой
    точки входа. Используются только закрытые минуты; задержка не больше одной
    минуты, зато одна и та же формирующаяся свеча не переигрывается после
    переноса стопа.
    """
    safe_limit = max(2, min(int(limit), 200))
    url = (
        f"{BASE}/v5/market/kline?category={category}&symbol={symbol}"
        f"&interval=1&limit={safe_limit}"
    )
    payload, report = fetch(url)
    rows = _result(payload).get("list") or []
    moment = now or datetime.now(UTC)
    return (
        _parse_kline_rows(
            rows,
            duration=timedelta(minutes=1),
            moment=moment,
            source="bybit-1m-live",
        ),
        report,
    )


def open_interest_series(
    symbol: str,
    *,
    interval: str = "1h",
    limit: int = 200,
    category: str = "linear",
    fetch=http_json,
) -> tuple[dict[datetime, Decimal], FetchReport]:
    """Ряд открытого интереса в номинале (§13)."""
    url = (
        f"{BASE}/v5/market/open-interest?category={category}&symbol={symbol}"
        f"&intervalTime={interval}&limit={limit}"
    )
    payload, report = fetch(url)
    rows = _result(payload).get("list") or []
    series: dict[datetime, Decimal] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        moment = _ms(row.get("timestamp"))
        value = _dec(row.get("openInterest"))
        if moment is not None and value is not None:
            series[moment] = value
    return series, report


def attach_open_interest(
    candles_: list[Candle], series: dict[datetime, Decimal]
) -> list[Candle]:
    """Приклеить OI к свечам по времени открытия.

    Совпадение точное. Ближайшее по времени значение подставлять нельзя:
    на границе часа это даёт сдвиг на целый бар, а именно на границах и
    происходит всё интересное.
    """
    from dataclasses import replace

    out = []
    for candle in candles_:
        value = series.get(candle.open_time)
        out.append(candle if value is None else replace(candle, open_interest=value))
    return out


def funding_percentile(rate: Decimal | None, history: list[Decimal]) -> float | None:
    """Перцентиль текущего фандинга за окно (§13).

    Экстремальный фандинг снижает оценку сделки в ту же сторону: платить за
    удержание позиции, когда за неё уже переплачивает весь рынок, — плохая
    идея независимо от красоты сетапа.
    """
    if rate is None or not history:
        return None
    below = sum(1 for h in history if h < rate)
    return below / len(history) * 100
