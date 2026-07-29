"""Адаптеры бирж на записанных фикстурах (engine-ТЗ §26.2).

Живых запросов здесь нет намеренно: тест, зависящий от биржи, краснеет по
выходным и в обед, и его перестают читать. Формы ответов взяты из клиента
приложения, который работает с ISS и Bybit больше месяца.

Проверяется не «разбор не падает», а то, что адаптер **не выдумывает**:
не переставляет ряд, не подставляет ноль вместо отсутствующего открытого
интереса, не переносит значение через дыру и не выдаёт ошибку биржи за
пустой рынок.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.market import crypto, moex
from app.market.http import FetchError, FetchReport, iss_rows
from app.models.enums import QualityFlag, Timeframe


def report(url: str = "test") -> FetchReport:
    return FetchReport(url=url, status=200, elapsed_ms=1, bytes_read=1, ok=True)


def fixed(payload):
    """Загрузчик, всегда отдающий одно и то же."""
    return lambda url: (payload, report(url))


def sequence(*payloads):
    """Загрузчик, отдающий ответы по очереди — для проверки постраничности."""
    box = list(payloads)

    def fetch(url):
        return (box.pop(0) if box else {}), report(url)

    return fetch


# ─── Разбор колоночного формата ISS ───────────────────────────────────────


def test_iss_rows_maps_columns_to_names():
    """Индексы колонок не должны просачиваться в код.

    Перепутанный индекс даёт правдоподобное, но неверное число — такую
    ошибку не видно до убытка.
    """
    payload = {
        "securities": {
            "columns": ["SECID", "SHORTNAME", "MINSTEP"],
            "data": [["SiU6", "Si-9.26", "1"], ["BRV6", "BR-10.26", "0.01"]],
        }
    }
    rows = iss_rows(payload, "securities")
    assert rows[0]["SECID"] == "SiU6"
    assert rows[1]["MINSTEP"] == "0.01"


def test_iss_rows_survives_garbage():
    assert iss_rows(None, "securities") == []
    assert iss_rows({"securities": []}, "securities") == []
    assert iss_rows({"securities": {"columns": None, "data": []}}, "securities") == []


# ─── Доска FORTS ──────────────────────────────────────────────────────────


FORTS_PAGE = {
    "securities": {
        "columns": ["SECID", "SHORTNAME", "LASTTRADEDATE", "MINSTEP", "STEPPRICE", "DECIMALS"],
        "data": [
            ["SiU6", "Si-9.26", "2026-09-17", "1", "1", 0],
            ["BRV6", "BR-10.26", "2026-10-01", "0.01", "6.5", 2],
        ],
    },
    "marketdata": {
        "columns": ["SECID", "LAST", "VALTODAY", "OPENPOSITION", "UPDATETIME"],
        "data": [
            ["SiU6", "90123", "15000000000", "412000", "18:45:00"],
            ["BRV6", "72.5", "3000000000", "88000", "18:45:00"],
        ],
    },
}


def test_forts_board_parses_open_interest():
    rows, _ = moex.forts_board(fetch=fixed(FORTS_PAGE))
    si = next(r for r in rows if r.sec_id == "SiU6")
    assert si.open_interest == Decimal(412000)
    assert si.turnover == Decimal(15000000000)
    assert si.last_trade_date == date(2026, 9, 17)


def test_forts_board_keeps_decimals_exact():
    """Шаг цены и его стоимость — множители в размере позиции (§17.1)."""
    rows, _ = moex.forts_board(fetch=fixed(FORTS_PAGE))
    brent = next(r for r in rows if r.sec_id == "BRV6")
    assert brent.min_step == Decimal("0.01")
    assert brent.step_price == Decimal("6.5")
    assert isinstance(brent.min_step, Decimal)


def test_pagination_stops_when_cursor_is_ignored():
    """Если биржа отдаёт ту же страницу, цикл обязан оборваться.

    Иначе он честно отработает сорок запросов и склеит сорок копий.
    """
    calls = {"n": 0}

    def fetch(url):
        calls["n"] += 1
        return FORTS_PAGE, report(url)

    rows, reports = moex.forts_board(fetch=fetch)
    assert len(rows) == 2                 # дубликаты отброшены
    assert calls["n"] <= 4                # два блока по одной-две страницы


def test_missing_market_data_does_not_invent_price():
    """Инструмент без котировки остаётся без неё, а не с нулём."""
    payload = {
        "securities": FORTS_PAGE["securities"],
        "marketdata": {"columns": ["SECID", "LAST", "VALTODAY", "OPENPOSITION", "UPDATETIME"],
                       "data": []},
    }
    rows, _ = moex.forts_board(fetch=fixed(payload))
    assert all(r.last is None and r.open_interest is None for r in rows)


# ─── Свечи MOEX ───────────────────────────────────────────────────────────


CANDLES = {
    "candles": {
        "columns": ["open", "close", "high", "low", "value", "volume", "begin", "end"],
        "data": [
            [90000, 90200, 90500, 89800, 1e9, 1200, "2026-07-29 10:00:00", "2026-07-29 10:59:59"],
            [90200, 90100, 90400, 90000, 9e8, 1100, "2026-07-29 11:00:00", "2026-07-29 11:59:59"],
        ],
    }
}


def test_candles_are_chronological_and_decimal():
    rows, _ = moex.candles("SiU6", Timeframe.H1, date(2026, 7, 1), fetch=fixed(CANDLES))
    assert [c.open_time.hour for c in rows] == [10, 11]
    assert rows[0].close == Decimal("90200")
    assert isinstance(rows[0].close, Decimal)


def test_candle_without_time_or_price_is_dropped():
    """Подставлять «сейчас» вместо неразобранной даты нельзя.

    Сдвиг ряда портит всё, что по нему считается, и не виден глазом.
    """
    broken = {
        "candles": {
            "columns": ["open", "close", "high", "low", "value", "volume", "begin"],
            "data": [
                [90000, 90200, 90500, 89800, 1e9, 1200, "не дата"],
                [90000, None, 90500, 89800, 1e9, 1200, "2026-07-29 10:00:00"],
                [90000, 90200, 90500, 89800, 1e9, 1200, "2026-07-29 11:00:00"],
            ],
        }
    }
    rows, _ = moex.candles("SiU6", Timeframe.H1, date(2026, 7, 1), fetch=fixed(broken))
    assert len(rows) == 1
    assert rows[0].open_time.hour == 11


def test_moex_refuses_four_hour_timeframe():
    """§4.4: молчаливая подмена таймфрейма — худший вид неверных данных.

    Результат выглядит правдоподобно, и заметить подмену невозможно.
    """
    with pytest.raises(ValueError, match="4H строится склейкой"):
        moex.candles("SiU6", Timeframe.H4, date(2026, 7, 1), fetch=fixed(CANDLES))


def test_moex_candles_are_flagged_without_open_interest():
    """ISS не отдаёт OI в свечах — свеча честно несёт флаг."""
    rows, _ = moex.candles("SiU6", Timeframe.H1, date(2026, 7, 1), fetch=fixed(CANDLES))
    assert all(c.open_interest is None for c in rows)
    assert all(QualityFlag.OI_UNAVAILABLE.value in c.quality_flags for c in rows)


# ─── Дневной открытый интерес ─────────────────────────────────────────────


HISTORY = {
    "history": {
        "columns": ["TRADEDATE", "OPENPOSITION"],
        "data": [["2026-07-27", "400000"], ["2026-07-28", "412000"]],
    }
}


def test_daily_open_interest_series():
    series, _ = moex.daily_open_interest("SiU6", date(2026, 7, 1), fetch=fixed(HISTORY))
    assert series[date(2026, 7, 28)] == Decimal(412000)


def test_attach_open_interest_does_not_carry_over_gaps():
    """Свеча без OI остаётся без него и сохраняет флаг.

    Перенос вчерашнего значения создаёт ряд, которого не было.
    """
    from app.market.candles import Candle

    daily = [
        Candle(datetime(2026, 7, 27, tzinfo=UTC), Decimal(90), Decimal(91), Decimal(89),
               Decimal(90), source="moex",
               quality_flags=(QualityFlag.OI_UNAVAILABLE.value,)),
        Candle(datetime(2026, 7, 29, tzinfo=UTC), Decimal(90), Decimal(91), Decimal(89),
               Decimal(90), source="moex",
               quality_flags=(QualityFlag.OI_UNAVAILABLE.value,)),
    ]
    series = {date(2026, 7, 27): Decimal(400000)}
    out = moex.attach_open_interest(daily, series)
    assert out[0].open_interest == Decimal(400000)
    assert QualityFlag.OI_UNAVAILABLE.value not in out[0].quality_flags
    assert out[1].open_interest is None
    assert QualityFlag.OI_UNAVAILABLE.value in out[1].quality_flags


def test_nearest_series_skips_expiring_contract():
    """§5.2: контракт с экспирацией через пару дней не берём.

    Держать позицию в истекающем контракте — отдельный риск, к сетапу
    отношения не имеющий.
    """
    rows, _ = moex.forts_board(fetch=fixed(FORTS_PAGE))
    assert moex.nearest_series(rows, "Si", date(2026, 9, 15)) is None
    assert moex.nearest_series(rows, "Si", date(2026, 7, 29)).sec_id == "SiU6"


# ─── Bybit ────────────────────────────────────────────────────────────────


TICKERS = {
    "retCode": 0,
    "result": {
        "list": [
            {
                "symbol": "BTCUSDT", "lastPrice": "61000.5", "turnover24h": "9500000000",
                "openInterestValue": "3200000000", "fundingRate": "0.0001",
                "nextFundingTime": "1785340800000",
                "bid1Price": "61000.0", "ask1Price": "61001.0",
            }
        ]
    },
}

KLINES = {
    "retCode": 0,
    "result": {
        "list": [
            ["1785333600000", "61200", "61400", "61100", "61300", "120", "7300000"],
            ["1785330000000", "61000", "61250", "60950", "61200", "100", "6100000"],
        ]
    },
}


def test_bybit_klines_are_reversed_into_chronological_order():
    """Биржа отдаёт от новых к старым.

    Перевёрнутый ряд не падает — он считает всё наоборот, и по одному числу
    это не видно.
    """
    rows, _ = crypto.klines("BTCUSDT", Timeframe.H1, fetch=fixed(KLINES))
    assert len(rows) == 2
    assert rows[0].open_time < rows[1].open_time
    assert rows[1].close == Decimal("61300")


def test_bybit_error_is_not_an_empty_market():
    """Биржа отвечает 200 и кладёт ошибку в тело.

    Без проверки кода отказ выглядел бы как «рынка нет».
    """
    with pytest.raises(ValueError, match="Bybit вернул ошибку"):
        crypto.tickers(fetch=fixed({"retCode": 10001, "retMsg": "param error"}))


def test_ticker_spread_is_relative():
    tickers, _ = crypto.tickers(fetch=fixed(TICKERS))
    btc = tickers[0]
    assert btc.open_interest == Decimal("3200000000")
    assert btc.funding_rate == Decimal("0.0001")
    spread = btc.relative_spread
    assert spread is not None and 0 < spread < 0.001


def test_ticker_without_quotes_has_no_spread():
    payload = {"retCode": 0, "result": {"list": [{"symbol": "X", "lastPrice": "1"}]}}
    tickers, _ = crypto.tickers(fetch=fixed(payload))
    assert tickers[0].relative_spread is None


def test_bybit_refuses_unsupported_timeframe():
    with pytest.raises(ValueError, match="не отдаёт таймфрейм"):
        crypto.klines("BTCUSDT", Timeframe.M10, fetch=fixed(KLINES))


def test_open_interest_attaches_only_on_exact_time():
    """Ближайшее по времени значение подставлять нельзя.

    На границе часа это сдвиг на целый бар, а на границах и происходит всё
    интересное.
    """
    rows, _ = crypto.klines("BTCUSDT", Timeframe.H1, fetch=fixed(KLINES))
    series = {rows[0].open_time: Decimal("3200000000")}
    out = crypto.attach_open_interest(rows, series)
    assert out[0].open_interest == Decimal("3200000000")
    assert out[1].open_interest is None


def test_funding_percentile():
    history = [Decimal("0.0001") * i for i in range(100)]
    assert crypto.funding_percentile(Decimal("0.0099"), history) > 90
    assert crypto.funding_percentile(Decimal("0.0001"), history) < 10
    assert crypto.funding_percentile(None, history) is None


# ─── Классификация отказов ────────────────────────────────────────────────


def test_fetch_error_carries_kind():
    """Таймаут, отказ соединения и 429 лечатся по-разному.

    Различать их надо в момент отказа, а не гадать потом по логам.
    """
    err = FetchError("timeout", "http://x", "нет ответа за 20 с")
    assert err.kind == "timeout"
    assert "нет ответа" in str(err)
