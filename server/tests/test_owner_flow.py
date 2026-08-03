"""Флоу владельца целиком, от сработавшей идеи до закрытой сделки.

Владелец сформулировал его сам и потребовал протестировать именно так:

    оперативно получил идею с максимумом информации → запустил бумажную
    сделку → она сама отслеживается и закрывается, не зависает до времён,
    пока срок жизни идеи закончился. Идеи в реализации должны ответственно
    отслеживаться: исполнение TP, переставление SL в безубыток, выход по
    SL, TP3.

Отдельный файл от `test_paper_tracker`, потому что проверяется другое. Там
— поведение сопровождения по шагам. Здесь — что все шаги соединены и
доходят до устройства: сделка видна через API, стоп на карточке тот,
который защищает позицию сейчас, а статус идеи следует за исполнением, а
не остаётся «Наблюдением». Каждое из этих звеньев по отдельности работало
и раньше; ломалось именно соединение, и владелец видел ровно это —
«идея всё ещё в наблюдении, хотя точка входа уже случилась».
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import get_db
from app.main import app
from app.models import Bar, PaperTrade, TradeIdea
from app.models.enums import IdeaStatus, PaperStatus, Timeframe
from app.paper.tracker import MAX_HOLD_DAYS, track
from tests.conftest import idea_kwargs

NOW = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def бар(instrument_id: str, index: int, low, high) -> Bar:
    return Bar(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        open_time=NOW + timedelta(hours=index),
        open=Decimal(str(high)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(high)),
        volume_units=Decimal("1000"),
        is_closed=True,
        source="test",
    )


def сработавшая_идея(session, instrument) -> TradeIdea:
    """LONG: вход 90100, стоп 89400, цели 91000 / 92000 / 93000."""
    base = idea_kwargs(instrument.instrument_id, NOW)
    base.update(status=IdeaStatus.TRIGGERED, quality_status="ACTIVE")
    idea = TradeIdea(**base)
    session.add(idea)
    session.flush()
    return idea


def прожить(session, instrument, шаги, *, start=1, hours=None):
    """Подать бары и прогнать сопровождение.

    `start` — номер первого бара: шаги подаются порциями, и повторно
    записать тот же час нельзя, у баров составной ключ.
    """
    for i, (low, high) in enumerate(шаги, start=start):
        session.add(бар(instrument.instrument_id, i, low, high))
    session.flush()
    последний = start + len(шаги) - 1
    return track(session, now=NOW + timedelta(hours=hours or последний + 1))


# ── Полный путь ─────────────────────────────────────────────────────────────


def test_путь_до_tp3_проходится_целиком(session, instrument, client):
    """Вход → TP1 → безубыток → TP2 → трейлинг → TP3 → закрытие."""
    idea = сработавшая_идея(session, instrument)

    прожить(session, instrument, [(90000, 90200)])  # вход
    trade = session.execute(
        __import__("sqlalchemy").select(PaperTrade)
    ).scalars().one()
    assert trade.status is PaperStatus.OPEN
    assert idea.status is IdeaStatus.FILLED

    прожить(session, instrument, [(90500, 91050)], start=2)  # TP1
    assert trade.tps_taken == 1
    assert trade.current_stop == trade.entry, "после первой цели стоп в безубытке"
    assert idea.status is IdeaStatus.TP1_HIT, "идея больше не «Наблюдение»"

    прожить(session, instrument, [(90800, 92050)], start=3)  # TP2
    assert trade.tps_taken == 2
    assert trade.current_stop == Decimal("91000"), "стоп подтянут к первой цели"

    прожить(session, instrument, [(92100, 93100)], start=4)  # TP3
    assert trade.status is PaperStatus.CLOSED
    assert trade.outcome == "TP"
    assert trade.realized_r > 0


def test_карточка_позиции_показывает_защиту_а_не_обещание(
    session, instrument, client
):
    """То, ради чего сделка переехала на сервер.

    На устройстве поле стопа было одно и неизменяемое, а перенос в
    безубыток жил во временной переменной внутри пересчёта: карточка всегда
    показывала исходный стоп, даже когда позиция была уже защищена.
    """
    сработавшая_идея(session, instrument)
    прожить(session, instrument, [(90000, 90200), (90500, 91050)])

    body = client.get("/api/v1/paper/trades").json()

    assert len(body) == 1
    row = body[0]
    assert row["at_breakeven"] is True
    assert Decimal(row["current_stop"]) == Decimal(row["entry"])
    assert Decimal(row["current_stop"]) != Decimal(row["initial_stop"])
    assert row["tps_taken"] == 1
    assert row["tps_total"] == 3
    # Цены строками: JSON-число это double, и шаг цены на другом конце
    # превращается в 0,004999999999999999.
    assert isinstance(row["entry"], str)


def test_молчание_источника_видно_на_карточке(session, instrument, client):
    """«Сверка не идёт» и «сделка спокойно живёт» выглядели одинаково.

    Именно так BTCUSDT провисел несколько дней на «взято тейков: 2 из 3»:
    Bybit ответил телефону 403, свечей не стало, сверка не вызывалась ни
    разу — а карточка осталась живой на вид.
    """
    сработавшая_идея(session, instrument)
    track(session, now=NOW)

    body = client.get("/api/v1/paper/trades").json()

    assert body[0]["stale_hours"] is not None
    assert body[0]["last_reconciled_at"] is None


def test_сделка_не_висит_до_истечения_срока_идеи(session, instrument):
    """Дословное требование владельца.

    Горизонт считался числом пришедших баров, поэтому инструмент, по
    которому свечи перестали приходить, давал бессмертную позицию.
    """
    сработавшая_идея(session, instrument)
    прожить(session, instrument, [(90000, 90200)])

    # Источник замолчал: новых баров нет вовсе.
    report = track(session, now=NOW + timedelta(days=MAX_HOLD_DAYS + 1))

    trade = session.execute(
        __import__("sqlalchemy").select(PaperTrade)
    ).scalars().one()
    assert trade.status is PaperStatus.CLOSED
    assert report.closed >= 1


def test_стоп_закрывает_и_идея_это_признаёт(session, instrument):
    """Идея с пройденным стопом висела в «Наблюдении» — жалоба владельца."""
    idea = сработавшая_идея(session, instrument)
    прожить(session, instrument, [(90000, 90200), (89300, 90000)])

    trade = session.execute(
        __import__("sqlalchemy").select(PaperTrade)
    ).scalars().one()
    assert trade.status is PaperStatus.CLOSED
    assert trade.outcome == "SL"
    assert idea.status is IdeaStatus.STOPPED


def test_владелец_может_закрыть_сделку_руками(session, instrument, client):
    """Автоматика не всесильна: источник может замолчать надолго.

    Причина записывается отдельной формулировкой — «закрыто владельцем» и
    «закрыто по стопу» в статистике сливаться не должны.
    """
    сработавшая_идея(session, instrument)
    track(session, now=NOW)
    trade_id = client.get("/api/v1/paper/trades").json()[0]["id"]

    body = client.post(f"/api/v1/paper/trades/{trade_id}/close").json()

    assert body["status"] == "CLOSED"
    assert body["outcome"] == "ручн."
    assert "владельцем" in body["close_reason"]
    # Повторное закрытие — конфликт, а не молчаливое согласие.
    assert client.post(f"/api/v1/paper/trades/{trade_id}/close").status_code == 409
