"""Сопровождение бумажной сделки (§18, §21).

Владелец сформулировал требование одной фразой: «она сама отслеживается и
закрывается, не зависает до времён, пока срок жизни идеи закончился». И
перечислил, что именно обязано отрабатывать: исполнение TP, перестановка
стопа в безубыток, выход по SL и TP3.

Проверяется здесь то, чего раньше не было вовсе. Стоп был объявлен
неизменяемым полем, а перенос в безубыток жил во временной переменной
внутри пересчёта — то есть не существовал ни в базе, ни на экране. Срок
считался числом пришедших баров, поэтому инструмент, по которому свечи
перестали приходить, давал бессмертную позицию: BTCUSDT провисел так
несколько дней на «взято тейков: 2 из 3».
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models import Bar, PaperTrade, TradeIdea
from app.models.enums import IdeaStatus, PaperStatus, Timeframe
from app.paper.tracker import MAX_HOLD_DAYS, advance, open_for, track
from tests.conftest import idea_kwargs

NOW = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)


def bar(instrument_id: str, index: int, low, high, close=None) -> Bar:
    return Bar(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        open_time=NOW + timedelta(hours=index),
        open=Decimal(str(high)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close if close is not None else high)),
        volume_units=Decimal("1000"),
        is_closed=True,
        source="test",
    )


def триггерная_идея(instrument_id: str, **overrides) -> TradeIdea:
    """LONG: вход 90100, стоп 89400, цели 91000 / 92000 / 93000."""
    base = idea_kwargs(instrument_id, NOW)
    base.update(status=IdeaStatus.TRIGGERED, quality_status="ACTIVE")
    base.update(overrides)
    return TradeIdea(**base)


def сделка(session, instrument, **overrides) -> PaperTrade:
    idea = триггерная_идея(instrument.instrument_id, **overrides)
    session.add(idea)
    session.flush()
    trade = open_for(session, idea, now=NOW)
    session.flush()
    return trade


# ── Открытие ────────────────────────────────────────────────────────────────


def test_сделка_заводится_по_сработавшей_идее(session, instrument):
    trade = сделка(session, instrument)

    assert trade is not None
    assert trade.status is PaperStatus.PENDING
    assert trade.current_stop == trade.initial_stop
    assert trade.tps_total_prices == 3 if hasattr(trade, "tps_total_prices") else True
    assert len(trade.tp_prices) == 3


def test_повторный_прогон_не_плодит_сделок(session, instrument):
    """Дедуп по идее, а не по инструменту.

    Дедуп по символу означал, что зависшая сделка блокирует запись всех
    последующих идей по тому же инструменту — владелец увидел это как
    «в журнале не все идеи».
    """
    idea = триггерная_идея(instrument.instrument_id)
    session.add(idea)
    session.flush()

    assert open_for(session, idea, now=NOW) is not None
    session.flush()
    assert open_for(session, idea, now=NOW) is None


# ── Ведение ─────────────────────────────────────────────────────────────────


def test_вход_исполняется_касанием_зоны(session, instrument):
    trade = сделка(session, instrument)
    advance(trade, [bar(instrument.instrument_id, 1, 90000, 90200)], now=NOW)

    assert trade.status is PaperStatus.OPEN


def test_первая_цель_переносит_стоп_в_безубыток(session, instrument):
    """Ровно то, что владелец потребовал и чего не было в базе.

    Перенос существовал только внутри пересчёта, во временной переменной,
    которая не сохранялась: на экране позиция всегда показывала исходный
    стоп, даже когда фактически была защищена.
    """
    trade = сделка(session, instrument)
    events = advance(
        trade,
        [
            bar(instrument.instrument_id, 1, 90000, 90200),
            bar(instrument.instrument_id, 2, 90500, 91050),
        ],
        now=NOW,
    )

    assert trade.tps_taken == 1
    assert trade.current_stop == trade.entry
    assert trade.breakeven_at is not None
    assert "стоп в безубытке" in events


def test_вторая_цель_подтягивает_стоп_к_первой(session, instrument):
    """Трейлинга не было вовсе: стоп вставал на вход один раз и застывал."""
    trade = сделка(session, instrument)
    advance(
        trade,
        [
            bar(instrument.instrument_id, 1, 90000, 90200),
            bar(instrument.instrument_id, 2, 90500, 92050),
        ],
        now=NOW,
    )

    assert trade.tps_taken == 2
    assert trade.current_stop == Decimal("91000")


def test_все_цели_закрывают_сделку(session, instrument):
    trade = сделка(session, instrument)
    advance(
        trade,
        [
            bar(instrument.instrument_id, 1, 90000, 90200),
            bar(instrument.instrument_id, 2, 90500, 93500),
        ],
        now=NOW,
    )

    assert trade.status is PaperStatus.CLOSED
    assert trade.outcome == "TP"
    assert trade.realized_r > 0


def test_стоп_закрывает_сделку_и_считает_убыток(session, instrument):
    trade = сделка(session, instrument)
    advance(
        trade,
        [
            bar(instrument.instrument_id, 1, 90000, 90200),
            bar(instrument.instrument_id, 2, 89300, 90000),
        ],
        now=NOW,
    )

    assert trade.status is PaperStatus.CLOSED
    assert trade.outcome == "SL"
    assert trade.realized_r < 0


def test_стоп_проверяется_раньше_цели(session, instrument):
    """Внутри одного бара порядок событий неизвестен.

    Выбирать выгодный — значит рисовать себе прибыль, которой не было.
    Свеча, задевшая и цель, и стоп, обязана считаться стопом.
    """
    trade = сделка(session, instrument)
    advance(
        trade,
        [
            bar(instrument.instrument_id, 1, 90000, 90200),
            bar(instrument.instrument_id, 2, 89300, 93500),
        ],
        now=NOW,
    )

    assert trade.outcome == "SL"


def test_после_безубытка_стоп_даёт_ноль_а_не_убыток(session, instrument):
    trade = сделка(session, instrument)
    advance(
        trade,
        [
            bar(instrument.instrument_id, 1, 90000, 90200),
            bar(instrument.instrument_id, 2, 90500, 91050),
            bar(instrument.instrument_id, 3, 90000, 90600),
        ],
        now=NOW,
    )

    assert trade.status is PaperStatus.CLOSED
    assert trade.outcome == "BE"
    # Первая цель уже забрана, остаток вышел в ноль — суммарно плюс.
    assert trade.realized_r > 0


# ── Протухание: то, чего не было вовсе ──────────────────────────────────────


def test_позиция_не_висит_вечно_при_молчащем_источнике(session, instrument):
    """Корень зависшей BTCUSDT.

    Горизонт считался числом пришедших баров. Bybit ответил телефону 403,
    баров не стало, сверка не вызывалась ни разу — и позиция замерла
    навсегда. Срок обязан течь по календарю, а не по данным.
    """
    trade = сделка(session, instrument)
    trade.status = PaperStatus.OPEN
    session.flush()

    report = track(session, now=NOW + timedelta(days=MAX_HOLD_DAYS + 1))

    assert trade.status is PaperStatus.CLOSED
    assert "дней" in trade.close_reason
    assert report.closed >= 1


def test_невыкупленная_заявка_протухает(session, instrument):
    trade = сделка(session, instrument)
    track(session, now=NOW + timedelta(days=30))

    assert trade.status is PaperStatus.CANCELLED
    assert trade.outcome == "отм."


def test_слепота_видна_поимённо(session, instrument):
    """«Сверка не идёт» и «сделка спокойно живёт» выглядят одинаково."""
    сделка(session, instrument)
    report = track(session, now=NOW + timedelta(hours=1))

    assert report.no_data == 1
    assert instrument.instrument_id in report.no_data_instruments
    assert instrument.instrument_id in report.summary()


# ── Связь с идеей ───────────────────────────────────────────────────────────


def test_идея_получает_живые_статусы_исполнения(session, instrument):
    """FILLED / TP1_HIT / MANAGING были объявлены и мертвы.

    Ни один файл сервера в них не переводил, поэтому исполняемая идея на
    устройстве показывалась «Наблюдением».
    """
    idea = триггерная_идея(instrument.instrument_id)
    session.add(idea)
    session.flush()
    for i, (low, high) in enumerate(
        [(90000, 90200), (90500, 91050)], start=1
    ):
        session.add(bar(instrument.instrument_id, i, low, high))
    session.flush()

    track(session, now=NOW + timedelta(hours=4))
    session.flush()

    assert idea.status is IdeaStatus.TP1_HIT
