"""Курс котировочной валюты к рублю (§17.1).

Проверяется не «функция вернула число», а три свойства, на которых стоит
размер позиции: курс снимается с торгуемой серии, устаревший курс не
используется, а незнакомая валюта не превращается молча в рубль.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.market import fx
from app.market.moex import FortsRow
from app.models import DataQualityEvent, FxRate

NOW = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)


def row(sec_id: str, last, turnover, expiry=None) -> FortsRow:
    return FortsRow(
        sec_id=sec_id,
        short_name=sec_id,
        last=None if last is None else Decimal(str(last)),
        turnover=None if turnover is None else Decimal(str(turnover)),
        open_interest=Decimal(1000),
        min_step=Decimal(1),
        step_price=Decimal(1),
        decimals=0,
        last_trade_date=expiry or (NOW.date() + timedelta(days=40)),
        updated_at="10:00:00",
        bid=None,
        ask=None,
    )


def test_dollar_rate_comes_from_si_divided_by_the_contract_size():
    """Si торгуется в рублях за 1000 долларов — курс это цена делить на 1000."""
    rates = fx.rates_from_board([row("SiU6", 78500, 1_000_000)])
    assert rates["USD"][0] == Decimal("78.5")
    assert "SiU6" in rates["USD"][1]


def test_rate_is_taken_from_the_series_where_trading_happens():
    """У ближней серии в день исполнения оборот падает, а цена застывает."""
    rows = [
        row("SiU6", 70000, 10, expiry=NOW.date() + timedelta(days=2)),
        row("SiZ6", 78500, 5_000_000, expiry=NOW.date() + timedelta(days=90)),
    ]
    assert fx.rates_from_board(rows)["USD"][0] == Decimal("78.5")


def test_series_without_price_is_not_a_rate():
    assert fx.rates_from_board([row("SiU6", None, 1_000_000)]) == {}


def test_root_prefix_does_not_catch_other_contracts():
    """`SILV` начинается на `SI`, но серебро — не доллар."""
    rates = fx.rates_from_board([row("SILVU6", 3200, 5_000_000)])
    assert "USD" not in rates


def test_empty_board_leaves_a_trace_instead_of_silence(session: Session):
    """Крипта без курса перестаёт торговаться — причина обязана быть видна."""
    saved = fx.record_rates(session, [row("RIU6", 300000, 1_000_000)], now=NOW)
    session.flush()
    assert saved == []
    events = list(
        session.query(DataQualityEvent).filter(DataQualityEvent.flag == "FX_MISSING")
    )
    assert len(events) == 1
    assert "курс к рублю не обновлён" in events[0].detail


def test_rate_is_overwritten_not_accumulated(session: Session):
    fx.record_rates(session, [row("SiU6", 78500, 1_000_000)], now=NOW)
    session.flush()
    fx.record_rates(
        session, [row("SiU6", 79000, 1_000_000)], now=NOW + timedelta(hours=4)
    )
    session.flush()
    rows = list(session.query(FxRate).filter(FxRate.currency == "USD"))
    assert len(rows) == 1
    assert rows[0].rate_rub == Decimal("79")


def test_usdt_is_read_through_the_dollar_and_says_so(session: Session):
    fx.record_rates(session, [row("SiU6", 78500, 1_000_000)], now=NOW)
    session.flush()
    assert fx.rate_to_rub(session, "USDT", now=NOW) == Decimal("78.5")
    note = fx.rate_note(session, "USDT", now=NOW)
    assert "USDT приравнен к USD" in note
    assert "78.5" in note


def test_rubles_need_no_rate(session: Session):
    assert fx.rate_to_rub(session, "RUB", now=NOW) == Decimal(1)
    assert fx.rate_note(session, "RUB", now=NOW) == ""


def test_unknown_currency_is_not_silently_a_ruble(session: Session):
    assert fx.rate_to_rub(session, "JPY", now=NOW) is None


def test_stale_rate_is_refused_not_used(session: Session):
    fx.record_rates(session, [row("SiU6", 78500, 1_000_000)], now=NOW)
    session.flush()
    fresh = NOW + fx.MAX_AGE + timedelta(hours=1)
    assert fx.rate_to_rub(session, "USDT", now=fresh) is None
    assert "неизвестен" not in fx.rate_note(session, "USDT", now=fresh)


def test_rate_older_than_a_day_is_used_but_flagged(session: Session):
    fx.record_rates(session, [row("SiU6", 78500, 1_000_000)], now=NOW)
    session.flush()
    later = NOW + timedelta(hours=40)
    assert fx.rate_to_rub(session, "USDT", now=later) == Decimal("78.5")
    assert "не обновлялся больше суток" in fx.rate_note(session, "USDT", now=later)


def test_missing_rate_note_names_the_currency(session: Session):
    assert "USDT" in fx.rate_note(session, "USDT", now=NOW)
