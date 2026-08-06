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

import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import event, func, select
from sqlalchemy.orm import sessionmaker

from app.models import Bar, Instrument, PaperTrade, TradeIdea
from app.models.enums import (
    AssetClass,
    IdeaStatus,
    PaperStatus,
    Timeframe,
    Venue,
)
from app.paper.tracker import MAX_HOLD_DAYS, advance, approve_for, open_for, track
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
    """Повтор явного подтверждения не создаёт вторую сделку."""
    idea = триггерная_идея(instrument.instrument_id)
    session.add(idea)
    session.flush()

    assert open_for(session, idea, now=NOW) is not None
    session.flush()
    assert open_for(session, idea, now=NOW) is None


def test_concurrent_approval_recovers_from_unique_constraint_race(engine):
    """Два одновременных approve возвращают одну и ту же paper-сделку.

    Второй поток проходит предварительный SELECT до фиксации первого и
    упирается именно в уникальный индекс. Savepoint обязан сохранить сессию
    рабочей, чтобы перечитать запись победителя вместо 500 и дубля.
    """
    suffix = uuid.uuid4().hex[:10].upper()
    instrument_id = f"TEST:FUT:RACE{suffix}"
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    seed = factory()
    first = factory()
    ready = threading.Event()
    proceed = threading.Event()
    result: dict[str, object] = {}
    idea_id = None
    worker: threading.Thread | None = None
    try:
        instrument = Instrument(
            instrument_id=instrument_id,
            venue=Venue.MOEX,
            asset_class=AssetClass.FUTURES,
            symbol=f"RACE{suffix}",
            title="Concurrent approval fixture",
            currency="RUB",
            tick_size=Decimal("1"),
            tick_value=Decimal("1"),
            lot_size=1,
            quantity_step=Decimal("1"),
            min_quantity=Decimal("1"),
            contract_multiplier=Decimal("1"),
            in_universe=True,
        )
        idea = триггерная_идея(instrument_id)
        seed.add_all([instrument, idea])
        seed.commit()
        idea_id = idea.id

        first_idea = first.get(TradeIdea, idea_id)
        first_trade, created = approve_for(first, first_idea, now=NOW)
        assert created is True

        def competing_approval() -> None:
            second = factory()
            paused = False

            @event.listens_for(second, "before_flush")
            def pause_before_insert(session, flush_context, instances):
                nonlocal paused
                if not paused and any(isinstance(row, PaperTrade) for row in session.new):
                    paused = True
                    ready.set()
                    proceed.wait(timeout=5)

            try:
                same_idea = second.get(TradeIdea, idea_id)
                trade, was_created = approve_for(second, same_idea, now=NOW)
                second.commit()
                result.update(id=str(trade.id), created=was_created)
            except Exception as exc:  # передать сбой в основной поток
                second.rollback()
                result["error"] = exc
            finally:
                second.close()

        worker = threading.Thread(target=competing_approval)
        worker.start()
        assert ready.wait(timeout=5), "второй approve не дошёл до INSERT"
        first.commit()
        proceed.set()
        worker.join(timeout=5)

        assert not worker.is_alive()
        assert "error" not in result, repr(result.get("error"))
        assert result == {"id": str(first_trade.id), "created": False}

        check = factory()
        try:
            count = check.scalar(
                select(func.count()).select_from(PaperTrade).where(
                    PaperTrade.idea_id == idea_id
                )
            )
            assert count == 1
        finally:
            check.close()
    finally:
        proceed.set()
        if worker is not None and worker.is_alive():
            worker.join(timeout=5)
        first.rollback()
        first.close()
        seed.close()
        if idea_id is not None:
            cleanup = factory()
            try:
                cleanup.query(PaperTrade).filter(PaperTrade.idea_id == idea_id).delete(
                    synchronize_session=False
                )
                cleanup.query(TradeIdea).filter(TradeIdea.id == idea_id).delete(
                    synchronize_session=False
                )
                cleanup.query(Instrument).filter(
                    Instrument.instrument_id == instrument_id
                ).delete(synchronize_session=False)
                cleanup.commit()
            finally:
                cleanup.close()


def test_approval_never_replays_bars_from_before_owner_decision(session, instrument):
    """Paper-вход не может исполниться задним числом до согласия владельца."""
    idea = триггерная_идея(
        instrument.instrument_id,
        signal_time=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=4),
    )
    session.add(idea)
    # Бар до approve касался входа и всех целей. Старое opened_at=signal_time
    # немедленно превращало подтверждение в уже закрытую выигрышную сделку.
    session.add(
        Bar(
            instrument_id=instrument.instrument_id,
            timeframe=Timeframe.H1,
            open_time=NOW - timedelta(hours=1),
            open=Decimal("90000"),
            high=Decimal("94000"),
            low=Decimal("89000"),
            close=Decimal("93000"),
            volume_units=Decimal("1000"),
            is_closed=True,
            source="test",
        )
    )
    session.flush()

    trade, created = approve_for(session, idea, now=NOW)
    track(session, now=NOW + timedelta(minutes=5))

    assert created is True
    assert trade.opened_at == NOW
    assert trade.status is PaperStatus.PENDING


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


def test_обработанный_бар_не_переигрывается(session, instrument):
    """Тот самый ложный стоп, который владелец увидел дважды.

    Граница выборки была включающей, и последний обработанный бар
    возвращался на каждом прогоне — а сопровождение идёт каждые пятнадцать
    минут при часовых барах, то есть один и тот же бар переигрывался
    четырежды.

    Безобидно это выглядит только на целях: они уже взяты и повторно не
    считаются. Но стоп проверяется по его **текущему** положению, а оно за
    этот же бар и переехало. Свеча, на которой вторая цель подтянула стоп
    к первой, на следующем прогоне оказывалась свечой, пробившей этот
    новый стоп: сделка закрывалась «по стопу» на движении в свою сторону.

    Владелец: «уже 2 раза выбило при том, что в целом направление сделок
    угадано, BTC бы дошёл до tp3».
    """
    trade = сделка(session, instrument)
    # Первый прогон: вход, затем свеча, дотянувшая до второй цели. Её низ
    # ниже будущего стопа — в этом вся соль.
    session.add(bar(instrument.instrument_id, 1, 90000, 90200))
    session.add(bar(instrument.instrument_id, 2, 90800, 92050))
    session.flush()
    track(session, now=NOW + timedelta(hours=3))

    assert trade.tps_taken == 2
    assert trade.current_stop == Decimal("91000")
    assert trade.status is PaperStatus.OPEN

    # Второй прогон без единого нового бара. Раньше здесь сделку закрывало.
    track(session, now=NOW + timedelta(hours=3, minutes=15))

    assert trade.status is PaperStatus.OPEN, "стоп сработал на уже прожитом баре"
    assert trade.outcome == ""


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


def test_ожидание_бара_не_называется_слепотой(session, instrument):
    """Крик, звучащий всё время, перестаёт значить что-либо.

    Сопровождение идёт каждые пятнадцать минут, бары часовые: в трёх
    прогонах из четырёх нового бара нет, и это нормальный такт. В живом
    логе это выглядело как «без баров 1 — надзор по ним слеп» при
    полностью исправном источнике.
    """
    trade = сделка(session, instrument)
    session.add(bar(instrument.instrument_id, 1, 90000, 90200))
    session.flush()
    track(session, now=NOW + timedelta(hours=2))
    assert trade.last_reconciled_at is not None

    # Пятнадцать минут спустя нового бара ещё нет — но источник жив.
    report = track(session, now=NOW + timedelta(hours=2, minutes=15))

    assert report.no_data == 0, "нормальный такт назван слепотой"
    assert report.awaiting_bar == 1
    assert "ждут нового бара" in report.summary()


def test_молчание_дольше_порога_остаётся_слепотой(session, instrument):
    """Источник, замолчавший всерьёз, обязан быть назван."""
    сделка(session, instrument)
    session.add(bar(instrument.instrument_id, 1, 90000, 90200))
    session.flush()
    track(session, now=NOW + timedelta(hours=2))

    report = track(session, now=NOW + timedelta(hours=8))

    assert report.no_data == 1
    assert report.awaiting_bar == 0


def test_вторая_идея_по_инструменту_сделки_не_открывает(session, instrument):
    """Тот самый экран владельца: десять позиций по HYPEUSDT подряд.

    Скан находит один и тот же сетап каждые пятнадцать минут, дедуп шёл по
    идее — и каждая находка становилась отдельной сделкой. Входы 52,2860 …
    52,2950: одна сделка, пересчитанная десять раз. Быть одновременно в
    десяти позициях по одному активу — не диверсификация, а
    десятикратный риск на нём.
    """
    первая = триггерная_идея(instrument.instrument_id)
    вторая = триггерная_идея(instrument.instrument_id)
    session.add_all([первая, вторая])
    session.flush()

    assert open_for(session, первая, now=NOW) is not None
    session.flush()
    assert open_for(session, вторая, now=NOW) is None


def test_triggered_ideas_do_not_open_without_owner_approval(session, instrument):
    """Серверный такт не подменяет явное решение владельца."""
    for _ in range(3):
        session.add(триггерная_идея(instrument.instrument_id))
    session.flush()

    report = track(session, now=NOW + timedelta(hours=1))

    assert report.opened == 0
    assert session.scalar(select(func.count()).select_from(PaperTrade)) == 0


# ── Связь с идеей ───────────────────────────────────────────────────────────


def test_идея_получает_живые_статусы_исполнения(session, instrument):
    """FILLED / TP1_HIT / MANAGING были объявлены и мертвы.

    Ни один файл сервера в них не переводил, поэтому исполняемая идея на
    устройстве показывалась «Наблюдением».
    """
    idea = триггерная_идея(instrument.instrument_id)
    session.add(idea)
    session.flush()
    assert open_for(session, idea, now=NOW) is not None
    session.flush()
    for i, (low, high) in enumerate(
        [(90000, 90200), (90500, 91050)], start=1
    ):
        session.add(bar(instrument.instrument_id, i, low, high))
    session.flush()

    track(session, now=NOW + timedelta(hours=4))
    session.flush()

    assert idea.status is IdeaStatus.TP1_HIT


# ── Схлопывание уже наплодившегося ──────────────────────────────────────────


def test_дубли_схлопываются_в_одну_живую(session, instrument):
    """То, что миграция делает с боевой базой владельца.

    На сервере уже лежали десять живых сделок по HYPEUSDT. Индекс по
    инструменту на таких данных не создастся, поэтому сначала схлопывание,
    потом ограничение — иначе миграция падает на боевой базе, а это хуже
    отсутствующей.
    """
    from sqlalchemy import text

    from app.paper.dedup import collapse_duplicates

    # Ограничение уже стоит (тесты гоняют настоящий alembic), поэтому
    # состояние «до миграции» воспроизводится буквально: индекс снимается
    # внутри откатываемой транзакции теста.
    session.execute(text("DROP INDEX uq_paper_live_per_instrument"))

    сделки = []
    for i in range(3):
        idea = триггерная_идея(instrument.instrument_id)
        session.add(idea)
        session.flush()
        trade = PaperTrade(
            idea_id=idea.id,
            instrument_id=instrument.instrument_id,
            direction=idea.direction,
            status=PaperStatus.PENDING,
            entry=Decimal("90100"),
            initial_stop=Decimal("89400"),
            current_stop=Decimal("89400"),
            tp_prices=["91000"],
            tp_shares=["1"],
            opened_at=NOW + timedelta(minutes=15 * i),
            expires_at=NOW + timedelta(days=5),
        )
        session.add(trade)
        сделки.append(trade)
    session.flush()

    снято = collapse_duplicates(session)
    session.expire_all()

    assert снято == 2
    живые = [t for t in сделки if t.status is PaperStatus.PENDING]
    assert len(живые) == 1
    # Осталась самая ранняя — та, которую владельцу показали первой.
    assert живые[0].opened_at.replace(tzinfo=UTC) == NOW
    снятые = [t for t in сделки if t.status is PaperStatus.CANCELLED]
    assert all(t.close_reason == "дубль по инструменту" for t in снятые)
    # Не CLOSED: позиции не было, и допуск к живым деньгам эти записи не
    # считает — он берёт только закрытые сделки.
    assert all(t.status is PaperStatus.CANCELLED for t in снятые)


def test_схлопывание_не_трогает_чужие_инструменты(session, instrument):
    """Вселенная — не один инструмент: соседа задеть нельзя."""
    from sqlalchemy import text

    from app.models import Instrument
    from app.paper.dedup import collapse_duplicates

    session.execute(text("DROP INDEX uq_paper_live_per_instrument"))
    сосед = Instrument(
        instrument_id="CRYPTO:PERP:XXXUSDT",
        venue=instrument.venue,
        asset_class=instrument.asset_class,
        symbol="XXXUSDT",
        currency=instrument.currency,
        tick_size=instrument.tick_size,
        tick_value=instrument.tick_value,
        quantity_step=instrument.quantity_step,
        min_quantity=instrument.min_quantity,
        contract_multiplier=instrument.contract_multiplier,
    )
    session.add(сосед)
    session.flush()

    одиночка = сделка(session, сосед)
    session.flush()

    assert collapse_duplicates(session) == 0
    session.expire_all()
    assert одиночка.status is PaperStatus.PENDING
