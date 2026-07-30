"""Загрузка рыночных данных в базу (engine-ТЗ §4, §26).

Три правила, которым подчинено всё в этом модуле:

1. **Пишутся только закрытые бары.** Незакрытый бар меняется сам по себе,
   и запись его в таблицу означает, что вчерашний расчёт сегодня даст другой
   результат — при том же коде и тех же настройках (§4.4).
2. **Повторная загрузка не портит данные.** Бар опознаётся по тройке
   инструмент-таймфрейм-время и обновляется, а не дублируется: планировщик
   перекрывает окна намеренно, чтобы не терять бары на границах.
3. **Отказ источника — событие, а не пустота.** Он пишется в
   `data_quality_events`, иначе просадка из-за дыры в данных неотличима в
   журнале от просадки из-за стратегии.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..models import Bar, DataQualityEvent, Instrument
from ..models.enums import AssetClass, QualityFlag, Timeframe, Venue
from . import crypto, moex
from .candles import Candle, detect_gaps
from .http import FetchError


@dataclass
class IngestReport:
    """Что удалось загрузить и что помешало."""

    instrument_id: str
    timeframe: Timeframe
    fetched: int = 0
    written: int = 0
    updated: int = 0
    gaps: int = 0
    error: str | None = None
    flags: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


def _record_failure(
    session: Session, source: str, instrument_id: str | None, flag: str, detail: str
) -> None:
    session.add(
        DataQualityEvent(
            source=source, instrument_id=instrument_id, flag=flag, detail=detail[:512]
        )
    )


def store_candles(
    session: Session,
    instrument_id: str,
    timeframe: Timeframe,
    candles: list[Candle],
) -> tuple[int, int]:
    """Записать бары идемпотентно.

    Возвращает (вставлено, обновлено). Обновление нужно: биржа уточняет
    объём и открытый интерес после закрытия сессии, и вторая загрузка того же
    бара должна приносить уточнение, а не конфликт.
    """
    closed = [c for c in candles if c.is_closed]
    if not closed:
        return 0, 0

    existing = {
        row[0]
        for row in session.execute(
            select(Bar.open_time).where(
                Bar.instrument_id == instrument_id,
                Bar.timeframe == timeframe,
                Bar.open_time.in_([c.open_time for c in closed]),
            )
        )
    }

    rows = [
        {
            "instrument_id": instrument_id,
            "timeframe": timeframe,
            "open_time": c.open_time,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume_units": c.volume_units,
            "volume_notional": c.volume_notional,
            "open_interest": c.open_interest,
            "is_closed": True,
            "source": c.source,
            "quality_flags": list(c.quality_flags),
        }
        for c in closed
    ]

    statement = insert(Bar).values(rows)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["instrument_id", "timeframe", "open_time"],
            set_={
                "open": statement.excluded.open,
                "high": statement.excluded.high,
                "low": statement.excluded.low,
                "close": statement.excluded.close,
                "volume_units": statement.excluded.volume_units,
                "volume_notional": statement.excluded.volume_notional,
                "open_interest": statement.excluded.open_interest,
                "quality_flags": statement.excluded.quality_flags,
            },
        )
    )
    session.flush()
    updated = len(existing)
    return len(rows) - updated, updated


# Скользящая проверка портфеля требует обучения на двух годах и измерения
# на следующем квартале — и так несколько окон подряд. Полгода истории для
# этого не хватает физически, поэтому дневки инвестиционных бумаг грузятся
# сразу за много лет. Один раз: дальше догружается только хвост.
_FIRST_LOAD_DAYS = 2200


def _board_path(instrument: Instrument) -> str:
    """Адрес свечей ISS для инструмента.

    У биржи свечи лежат под своим рынком и своей доской: акции и фонды — на
    ``shares``, ОФЗ — на ``bonds``, фьючерсы — на ``forts``. Запрос акции по
    адресу облигаций возвращает пустой список — то есть выглядит как «бумага
    не торговалась», а не как ошибка адреса. Поэтому рынок и доска
    записываются в справочник при синхронизации и читаются отсюда.
    """
    if instrument.asset_class is AssetClass.FUTURES:
        return moex.FORTS
    meta = instrument.metadata_json or {}
    market, board = meta.get("market"), meta.get("board")
    if market and board:
        return f"engines/stock/markets/{market}/boards/{board}"
    return moex.SHARES


def _since(
    session: Session,
    instrument: Instrument,
    timeframe: Timeframe,
    *,
    moment: datetime,
    days: int,
) -> date:
    """С какой даты просить свечи.

    Есть история — берётся её хвост с перекрытием: биржа уточняет последние
    бары, и без перекрытия эти уточнения не приедут никогда. Истории нет —
    берётся вся глубина, разная для инвестиций и для срочного рынка.
    """
    newest = session.execute(
        select(func.max(Bar.open_time)).where(
            Bar.instrument_id == instrument.instrument_id,
            Bar.timeframe == timeframe,
        )
    ).scalar_one_or_none()
    if newest is not None:
        return (newest - timedelta(days=5)).date()
    depth = days
    if timeframe is Timeframe.D1 and instrument.asset_class in _DAILY_ONLY:
        depth = _FIRST_LOAD_DAYS
    return (moment - timedelta(days=depth)).date()


def ingest_moex(
    session: Session,
    instrument: Instrument,
    timeframe: Timeframe,
    *,
    days: int = 120,
    now: datetime | None = None,
    fetch=None,
) -> IngestReport:
    """Загрузить свечи MOEX и, для дневок, приклеить открытый интерес."""
    moment = now or datetime.now(UTC)
    report = IngestReport(instrument.instrument_id, timeframe)
    sec_id = instrument.symbol
    since = _since(session, instrument, timeframe, moment=moment, days=days)

    kwargs = {"fetch": fetch} if fetch else {}
    path = _board_path(instrument)

    try:
        candles, _ = moex.candles(sec_id, timeframe, since, path=path, **kwargs)
    except FetchError as exc:
        report.error = f"{exc.kind}: {exc.detail}"
        _record_failure(session, "moex", instrument.instrument_id, exc.kind, exc.detail)
        return report
    except ValueError as exc:
        report.error = str(exc)
        return report

    # Открытый интерес есть только на срочном рынке и только дневным рядом.
    if timeframe is Timeframe.D1 and instrument.asset_class is AssetClass.FUTURES:
        try:
            series, _ = moex.daily_open_interest(sec_id, since, **kwargs)
            candles = moex.attach_open_interest(candles, series)
        except FetchError as exc:
            # Не роняем загрузку цен из-за отсутствия OI: цены важнее, а
            # отсутствие OI честно останется флагом на барах.
            report.flags.append(QualityFlag.OI_UNAVAILABLE.value)
            _record_failure(
                session, "moex", instrument.instrument_id,
                QualityFlag.OI_UNAVAILABLE.value, exc.detail,
            )

    report.fetched = len(candles)
    report.gaps = len(detect_gaps(candles, timeframe))
    if report.gaps:
        report.flags.append(QualityFlag.GAP.value)
        _record_failure(
            session, "moex", instrument.instrument_id, QualityFlag.GAP.value,
            f"дыр в ряду {timeframe.value}: {report.gaps}",
        )
    report.written, report.updated = store_candles(
        session, instrument.instrument_id, timeframe, candles
    )
    return report


def ingest_crypto(
    session: Session,
    instrument: Instrument,
    timeframe: Timeframe,
    *,
    limit: int = 400,
    fetch=None,
) -> IngestReport:
    """Загрузить свечи криптобиржи вместе с рядом открытого интереса."""
    report = IngestReport(instrument.instrument_id, timeframe)
    kwargs = {"fetch": fetch} if fetch else {}

    try:
        candles, _ = crypto.klines(instrument.symbol, timeframe, limit=limit, **kwargs)
    except FetchError as exc:
        report.error = f"{exc.kind}: {exc.detail}"
        _record_failure(session, "bybit", instrument.instrument_id, exc.kind, exc.detail)
        return report
    except ValueError as exc:
        report.error = str(exc)
        _record_failure(session, "bybit", instrument.instrument_id, "rejected", str(exc))
        return report

    interval = {Timeframe.H1: "1h", Timeframe.H4: "4h", Timeframe.D1: "1d"}.get(timeframe)
    if interval:
        try:
            series, _ = crypto.open_interest_series(
                instrument.symbol, interval=interval, **kwargs
            )
            candles = crypto.attach_open_interest(candles, series)
        except (FetchError, ValueError) as exc:
            report.flags.append(QualityFlag.OI_UNAVAILABLE.value)
            _record_failure(
                session, "bybit", instrument.instrument_id,
                QualityFlag.OI_UNAVAILABLE.value, str(exc),
            )

    report.fetched = len(candles)
    report.gaps = len(detect_gaps(candles, timeframe))
    if report.gaps:
        report.flags.append(QualityFlag.GAP.value)
    report.written, report.updated = store_candles(
        session, instrument.instrument_id, timeframe, candles
    )
    return report


# Инвестиционный контур живёт на дневках (§6): решение о составе принимается
# по закрытиям дня, часовой ряд там не читает никто.
_DAILY_ONLY = frozenset(
    {
        AssetClass.MONEY_MARKET,
        AssetClass.BOND_FUND,
        AssetClass.OFZ,
        AssetClass.CORPORATE_BOND,
        AssetClass.EQUITY,
        AssetClass.GOLD,
    }
)


def _timeframes_for(instrument: Instrument) -> list[Timeframe]:
    if instrument.asset_class in _DAILY_ONLY:
        return [Timeframe.D1]
    return [Timeframe.D1, Timeframe.H1]


def ingest_instrument(
    session: Session,
    instrument: Instrument,
    timeframes: list[Timeframe] | None = None,
    *,
    now: datetime | None = None,
    fetch=None,
) -> list[IngestReport]:
    """Загрузить все нужные конвейеру таймфреймы одного инструмента.

    Инвестиционным бумагам часовой ряд не нужен: пакет держится месяцами и
    считается по дневным закрытиям. Загружать его — это десятки лишних
    запросов к бирже за данными, которые никто не прочитает.
    """
    wanted = timeframes or _timeframes_for(instrument)
    reports = []
    for timeframe in wanted:
        if instrument.venue is Venue.MOEX:
            reports.append(
                ingest_moex(session, instrument, timeframe, now=now, fetch=fetch)
            )
        else:
            reports.append(ingest_crypto(session, instrument, timeframe, fetch=fetch))
    return reports


def ingest_universe(
    session: Session,
    *,
    timeframes: list[Timeframe] | None = None,
    now: datetime | None = None,
    fetch=None,
) -> list[IngestReport]:
    """Загрузить всю вселенную.

    Отказ одного инструмента не прекращает загрузку остальных: одна
    недоступная бумага не должна лишать владельца выдачи дня.
    """
    instruments = list(
        session.execute(
            select(Instrument).where(Instrument.in_universe.is_(True))
        ).scalars()
    )
    reports: list[IngestReport] = []
    for instrument in instruments:
        try:
            reports.extend(
                ingest_instrument(
                    session, instrument, timeframes, now=now, fetch=fetch
                )
            )
        except Exception as exc:  # источник может удивить чем угодно
            report = IngestReport(instrument.instrument_id, Timeframe.D1)
            report.error = f"{type(exc).__name__}: {exc}"
            reports.append(report)
            _record_failure(
                session, instrument.venue.value.lower(), instrument.instrument_id,
                "SOURCE_CONFLICT", str(exc),
            )
    return reports
