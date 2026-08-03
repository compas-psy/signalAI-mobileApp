"""Слепота обязана называть причину, а не только имя (§4.4, §24).

Из живого лога сервера:

    supervise: ок — проверено 12, без баров 2
    (CRYPTO:PERP:HYPEUSDT, CRYPTO:PERP:HYPEUSDT) — надзор по ним слеп
    paper: ок — сверено 0, открыто 1, без баров 1 (CRYPTO:PERP:HYPEUSDT)

Две беды в одной строке. Инструмент назван дважды, потому что счёт идёт по
идеям, а перечисление читается как перечисление инструментов. И ни слова о
том, почему баров нет, — хотя причина уже записана загрузкой в
``data_quality_events``. «Биржа не знает символа», «биржа ответила 429» и
«инструмент только что попал в отбор» требуют разных действий, и первые две
чинятся.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.market.blindness import annotate, reasons
from app.models import DataQualityEvent

NOW = datetime(2026, 8, 3, 21, 0, tzinfo=UTC)


def отказ(session, instrument_id: str, flag: str, detail: str, *, ago_days=0):
    event = DataQualityEvent(
        source="bybit",
        instrument_id=instrument_id,
        flag=flag,
        detail=detail,
    )
    session.add(event)
    session.flush()
    event.occurred_at = NOW - timedelta(days=ago_days)
    session.flush()
    return event


def test_причина_доходит_до_строки_журнала(session):
    отказ(session, "CRYPTO:PERP:HYPEUSDT", "rejected", "биржа не знает символа")

    found = reasons(session, ["CRYPTO:PERP:HYPEUSDT"], now=NOW)

    assert "не знает символа" in found["CRYPTO:PERP:HYPEUSDT"]


def test_берётся_последний_отказ_а_не_первый(session):
    отказ(session, "CRYPTO:PERP:HYPEUSDT", "http", "429", ago_days=3)
    отказ(session, "CRYPTO:PERP:HYPEUSDT", "rejected", "символ снят с торгов")

    found = reasons(session, ["CRYPTO:PERP:HYPEUSDT"], now=NOW)

    assert "снят с торгов" in found["CRYPTO:PERP:HYPEUSDT"]


def test_старый_отказ_сегодняшнюю_слепоту_не_объясняет(session):
    # Отказ месячной давности, после которого бары приходили нормально,
    # объясняет прошлое молчание, а не текущее.
    отказ(session, "CRYPTO:PERP:HYPEUSDT", "http", "429", ago_days=40)

    assert reasons(session, ["CRYPTO:PERP:HYPEUSDT"], now=NOW) == {}


def test_инструмент_без_записанного_отказа_причины_не_получает(session):
    """Отсутствие причины — тоже сведения.

    Подставить «источник не ответил» значит утверждать то, чего никто не
    наблюдал: загрузка по этому инструменту могла просто не запускаться.
    """
    line = annotate(session, ["CRYPTO:PERP:NEWCOINUSDT"], now=NOW)

    assert line == "CRYPTO:PERP:NEWCOINUSDT"


def test_перечисление_смешивает_известное_и_неизвестное(session):
    отказ(session, "CRYPTO:PERP:HYPEUSDT", "rejected", "биржа не знает символа")

    line = annotate(
        session,
        ["CRYPTO:PERP:HYPEUSDT", "CRYPTO:PERP:NEWCOINUSDT"],
        now=NOW,
    )

    assert "HYPEUSDT — rejected: биржа не знает символа" in line
    assert "CRYPTO:PERP:NEWCOINUSDT" in line


def test_длинный_список_обрезается_и_говорит_об_этом(session):
    names = [f"CRYPTO:PERP:C{i}USDT" for i in range(6)]

    line = annotate(session, names, now=NOW)

    # Молчаливая обрезка читается как «всего трое», а их шестеро.
    assert "и ещё 3" in line
