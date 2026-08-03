"""Загрузка данных и планировщик §4, §26."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.market.http import FetchError, FetchReport
from app.market.ingest import (
    ingest_crypto,
    ingest_moex,
    ingest_universe,
    store_candles,
)
from app.market.candles import Candle
from app.models import Bar, DataQualityEvent, Instrument
from app.models.enums import AssetClass, QualityFlag, Timeframe, Venue
from app.scheduler.runner import (
    Scheduler,
    build_default_scheduler,
    latest_bar_time,
)

NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)


def report(url="t"):
    return FetchReport(url=url, status=200, elapsed_ms=1, bytes_read=1, ok=True)


def fixed(payload):
    return lambda url: (payload, report(url))


def candle(hour: int, close: str = "100", closed: bool = True) -> Candle:
    price = Decimal(close)
    return Candle(
        open_time=datetime(2026, 7, 29, hour, tzinfo=UTC),
        open=price, high=price + 1, low=price - 1, close=price,
        volume_units=Decimal(10), is_closed=closed, source="test",
    )


def make_instrument(session, **over) -> Instrument:
    data = dict(
        instrument_id="MOEX:FUT:SIU6", venue=Venue.MOEX,
        asset_class=AssetClass.FUTURES, symbol="SiU6",
        tick_size=Decimal(1), tick_value=Decimal(1),
        quantity_step=Decimal(1), min_quantity=Decimal(1),
        contract_multiplier=Decimal(1), in_universe=True,
    )
    data.update(over)
    item = Instrument(**data)
    session.add(item)
    session.flush()
    return item


# ─── Запись баров ─────────────────────────────────────────────────────────


def test_only_closed_bars_are_stored(session, instrument):
    """§4.4: незакрытый бар меняется сам по себе.

    Записав его, мы получим вчерашний расчёт с сегодняшним результатом при
    том же коде и тех же настройках.
    """
    written, updated = store_candles(
        session, instrument.instrument_id, Timeframe.H1,
        [candle(10), candle(11, closed=False)],
    )
    assert written == 1 and updated == 0
    stored = session.execute(select(Bar)).scalars().all()
    assert len(stored) == 1
    assert stored[0].open_time.hour == 10


def test_repeated_ingest_updates_instead_of_duplicating(session, instrument):
    """Планировщик перекрывает окна намеренно — повтор не должен ломать данные."""
    store_candles(session, instrument.instrument_id, Timeframe.H1, [candle(10, "100")])
    written, updated = store_candles(
        session, instrument.instrument_id, Timeframe.H1, [candle(10, "105")]
    )
    assert written == 0 and updated == 1

    stored = session.execute(select(Bar)).scalars().all()
    assert len(stored) == 1
    assert stored[0].close == Decimal(105)   # уточнение приехало


def test_reingest_refines_volume_and_open_interest(session, instrument):
    """Биржа уточняет объём и OI после закрытия сессии."""
    from dataclasses import replace

    first = candle(10)
    store_candles(session, instrument.instrument_id, Timeframe.H1, [first])
    refined = replace(first, volume_units=Decimal(999), open_interest=Decimal(4000))
    store_candles(session, instrument.instrument_id, Timeframe.H1, [refined])

    stored = session.execute(select(Bar)).scalars().one()
    assert stored.volume_units == Decimal(999)
    assert stored.open_interest == Decimal(4000)


def test_empty_input_writes_nothing(session, instrument):
    assert store_candles(session, instrument.instrument_id, Timeframe.H1, []) == (0, 0)


# ─── Загрузка MOEX ────────────────────────────────────────────────────────


CANDLES = {
    "candles": {
        "columns": ["open", "close", "high", "low", "value", "volume", "begin"],
        "data": [
            [90000, 90200, 90500, 89800, 1e9, 1200, "2026-07-28 00:00:00"],
            [90200, 90100, 90400, 90000, 9e8, 1100, "2026-07-29 00:00:00"],
        ],
    }
}

HISTORY = {
    "history": {
        "columns": ["TRADEDATE", "OPENPOSITION"],
        "data": [["2026-07-28", "400000"], ["2026-07-29", "412000"]],
    }
}


def test_moex_daily_ingest_attaches_open_interest(session):
    inst = make_instrument(session)

    def fetch(url):
        return (HISTORY if "history" in url else CANDLES), report(url)

    result = ingest_moex(session, inst, Timeframe.D1, now=NOW, fetch=fetch)
    assert result.ok and result.written == 2

    bars = session.execute(select(Bar).order_by(Bar.open_time)).scalars().all()
    assert bars[1].open_interest == Decimal(412000)
    assert QualityFlag.OI_UNAVAILABLE.value not in (bars[1].quality_flags or [])


def test_missing_open_interest_does_not_lose_prices(session):
    """Цены важнее OI: отсутствие второго не отменяет первое."""
    inst = make_instrument(session)

    def fetch(url):
        if "history" in url:
            raise FetchError("timeout", url, "нет ответа")
        return CANDLES, report(url)

    result = ingest_moex(session, inst, Timeframe.D1, now=NOW, fetch=fetch)
    assert result.ok
    assert result.written == 2
    assert QualityFlag.OI_UNAVAILABLE.value in result.flags

    events = session.execute(select(DataQualityEvent)).scalars().all()
    assert any(e.flag == QualityFlag.OI_UNAVAILABLE.value for e in events)


def test_source_failure_is_an_event_not_silence(session):
    """Просадка из-за дыры в данных не должна выглядеть как просадка стратегии."""
    inst = make_instrument(session)

    def fetch(url):
        raise FetchError("unreachable", url, "биржа не отвечает")

    result = ingest_moex(session, inst, Timeframe.D1, now=NOW, fetch=fetch)
    assert not result.ok
    assert "unreachable" in result.error

    events = session.execute(select(DataQualityEvent)).scalars().all()
    assert len(events) == 1
    assert events[0].source == "moex"
    assert "не отвечает" in events[0].detail


def test_пустой_успешный_ответ_записывается_как_событие(session):
    """Дыра, найденная по живому логу.

    Отказ загрузки записывался, а пустой успешный ответ — нет: биржа
    отвечает 200 с пустым списком, исключения нет, и в журнале не остаётся
    ничего. Инструмент при этом сидит без баров, надзор по нему слеп, а на
    вопрос «почему» ответить нечем:

        supervise: проверено 17, без баров 4 (CRYPTO:PERP:HYPEUSDT)
        — надзор по ним слеп
    """
    inst = make_instrument(session)
    пусто = {"candles": {"columns": CANDLES["candles"]["columns"], "data": []}}

    result = ingest_moex(session, inst, Timeframe.D1, now=NOW, fetch=fixed(пусто))

    assert result.ok, "это не отказ загрузки: источник ответил"
    assert result.written == 0
    events = session.execute(select(DataQualityEvent)).scalars().all()
    assert [e.flag for e in events] == ["empty"]
    assert "свечей" in events[0].detail


def test_unsupported_timeframe_is_reported_not_substituted(session):
    """4H у ISS нет, и подменять его часовым нельзя."""
    inst = make_instrument(session)
    result = ingest_moex(session, inst, Timeframe.H4, now=NOW, fetch=fixed(CANDLES))
    assert not result.ok
    assert "4H строится склейкой" in result.error


# ─── Загрузка крипты ──────────────────────────────────────────────────────


KLINES = {
    "retCode": 0,
    "result": {
        "list": [
            ["1785333600000", "61200", "61400", "61100", "61300", "120", "7300000"],
            ["1785330000000", "61000", "61250", "60950", "61200", "100", "6100000"],
        ]
    },
}

OI = {
    "retCode": 0,
    "result": {"list": [{"timestamp": "1785333600000", "openInterest": "3200000000"}]},
}


def test_crypto_ingest_writes_chronologically(session):
    inst = make_instrument(
        session, instrument_id="CRYPTO:PERP:BTCUSDT", venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL, symbol="BTCUSDT", currency="USDT",
    )

    def fetch(url):
        return (OI if "open-interest" in url else KLINES), report(url)

    result = ingest_crypto(session, inst, Timeframe.H1, fetch=fetch)
    assert result.ok and result.written == 2

    bars = session.execute(select(Bar).order_by(Bar.open_time)).scalars().all()
    assert bars[0].open_time < bars[1].open_time
    assert bars[1].open_interest == Decimal("3200000000")


def test_exchange_error_becomes_an_event(session):
    inst = make_instrument(
        session, instrument_id="CRYPTO:PERP:BTCUSDT", venue=Venue.CRYPTO,
        asset_class=AssetClass.CRYPTO_PERPETUAL, symbol="BTCUSDT",
    )
    result = ingest_crypto(
        session, inst, Timeframe.H1,
        fetch=fixed({"retCode": 10001, "retMsg": "bad symbol"}),
    )
    assert not result.ok
    assert session.execute(select(DataQualityEvent)).scalars().all()


def test_one_broken_instrument_does_not_stop_the_rest(session):
    """Одна недоступная бумага не должна лишать владельца выдачи дня."""
    make_instrument(session, instrument_id="MOEX:FUT:A", symbol="A")
    make_instrument(session, instrument_id="MOEX:FUT:B", symbol="B")

    calls = {"n": 0}

    def fetch(url):
        calls["n"] += 1
        if "/A/" in url:
            raise FetchError("timeout", url, "нет ответа")
        return CANDLES, report(url)

    reports = ingest_universe(session, timeframes=[Timeframe.D1], fetch=fetch)
    assert len(reports) == 2
    assert any(not r.ok for r in reports)
    assert any(r.ok and r.written for r in reports)


# ─── Планировщик §26 ──────────────────────────────────────────────────────


def test_jobs_run_only_when_due(session):
    scheduler = Scheduler()
    runs = []
    scheduler.add("job", timedelta(minutes=15), lambda s: runs.append(1) or "ок")

    scheduler.tick(session, now=NOW, autocommit=False)
    scheduler.tick(session, now=NOW + timedelta(minutes=5), autocommit=False)
    assert len(runs) == 1

    scheduler.tick(session, now=NOW + timedelta(minutes=20), autocommit=False)
    assert len(runs) == 2


def test_failing_job_does_not_stop_the_others(session):
    """Сломавшаяся загрузка одной площадки не отменяет пересчёт по другой."""
    scheduler = Scheduler()
    ok_runs = []

    def broken(s):
        raise RuntimeError("источник упал")

    scheduler.add("broken", timedelta(minutes=1), broken)
    scheduler.add("healthy", timedelta(minutes=1), lambda s: ok_runs.append(1) or "ок")

    results = scheduler.tick(session, now=NOW, autocommit=False)
    assert len(results) == 2
    assert [r.ok for r in results] == [False, True]
    assert "источник упал" in results[0].detail
    assert len(ok_runs) == 1


def test_scan_is_skipped_when_market_did_not_move(session, instrument):
    """Календарь торгов заменяет сам поток котировок.

    Рынок закрыт — новых баров нет — скан не идёт: он дал бы тот же ответ, а
    идеи продублировались бы в журнале.
    """
    store_candles(session, instrument.instrument_id, Timeframe.H1, [candle(10)])
    scheduler = build_default_scheduler(fetch=fixed({}))

    scan_job = next(j for j in scheduler.jobs if j.name == "scan")
    first = scan_job.run(session)
    second = scan_job.run(session)

    assert "просмотрено" in first
    assert "новых баров нет" in second


def test_scan_says_when_there_are_no_bars_at_all(session):
    scheduler = build_default_scheduler(fetch=fixed({}))
    scan_job = next(j for j in scheduler.jobs if j.name == "scan")
    assert "сканировать нечего" in scan_job.run(session)


def test_latest_bar_time_ignores_unclosed(session, instrument):
    store_candles(
        session, instrument.instrument_id, Timeframe.H1,
        [candle(10), candle(11, closed=False)],
    )
    assert latest_bar_time(session).hour == 10


def test_history_is_bounded(session):
    """Планировщик работает сутками — история не должна расти бесконечно."""
    scheduler = Scheduler(max_history=5)
    scheduler.add("job", timedelta(seconds=0), lambda s: "ок")
    for i in range(20):
        scheduler.tick(session, now=NOW + timedelta(minutes=i), autocommit=False)
    assert len(scheduler.history) == 5
