"""Конвейер сканирования целиком: от свечей в базе до идеи в базе.

Интеграционная проверка §28: scheduler → scan → idea. Здесь важно не то, что
идея нашлась (на синтетике это лотерея), а что конвейер **никогда не молчит**:
каждый инструмент либо доходит до идеи, либо попадает в отказы с причиной.
Молчаливая потеря инструмента неотличима от поломки движка.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.models import Bar, IdeaEvent, Instrument, TradeIdea
from app.models.enums import AssetClass, IdeaStatus, Venue
from app.pipeline.scan import scan, scan_instrument
from app.risk.sizing import RiskState

NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)


def add_instrument(session, iid="MOEX:FUT:TEST", **over) -> Instrument:
    data = dict(
        instrument_id=iid,
        venue=Venue.MOEX,
        asset_class=AssetClass.FUTURES,
        symbol=iid.split(":")[-1],
        tick_size=Decimal(1),
        tick_value=Decimal(1),
        lot_size=1,
        quantity_step=Decimal(1),
        min_quantity=Decimal(1),
        contract_multiplier=Decimal(1),
        correlation_cluster="rub_fx",
        in_universe=True,
        is_tradable=True,
    )
    data.update(over)
    item = Instrument(**data)
    session.add(item)
    session.flush()
    return item


def add_bars(session, iid: str, timeframe: str, series, start: datetime, step: timedelta):
    """series — список кортежей (open, high, low, close, volume)."""
    for i, (o, h, low, c, v) in enumerate(series):
        session.add(
            Bar(
                instrument_id=iid,
                timeframe=timeframe,
                open_time=start + step * i,
                open=Decimal(str(o)), high=Decimal(str(h)),
                low=Decimal(str(low)), close=Decimal(str(c)),
                volume_units=Decimal(str(v)),
                volume_notional=Decimal(str(v)) * Decimal(str(c)),
                is_closed=True,
                source="test",
            )
        )
    session.flush()


def uptrend_daily(n: int = 200):
    """Восходящий ряд: цена растёт, бары узкие."""
    out, price = [], 100.0
    for i in range(n):
        out.append((price, price + 1.5, price - 1.0, price + 1.0, 1_000_000))
        price += 1.0
    return out


def uptrend_hourly(n: int = 400):
    """Часовой ряд с откатом в конце — материал для сетапа."""
    out, price = [], 100.0
    for i in range(n):
        # Растём, потом последние 40 баров откатываемся.
        drift = 0.5 if i < n - 40 else -0.6
        out.append((price, price + 1.0, price - 0.8, price + drift, 5_000))
        price += drift
    return out


# ─── Конвейер не молчит ───────────────────────────────────────────────────


def test_scan_on_empty_universe_is_not_an_error(session):
    """Пустая вселенная — валидный результат, а не исключение."""
    result = scan(session, now=NOW)
    assert result.scanned == 0
    assert result.produced == 0
    assert result.daily is not None
    assert "не создаёт сделки ради нормы" in result.daily.no_trade_reason


def test_instrument_without_data_is_skipped_with_reason(session):
    """Инструмент без истории обязан назвать, чего именно не хватило."""
    add_instrument(session)
    result = scan(session, now=NOW)
    assert result.scanned == 1
    assert result.produced == 0
    assert len(result.skipped) == 1
    skip = result.skipped[0]
    assert skip.stage == "данные"
    assert "дневных баров 0" in skip.reason and "нужно" in skip.reason


def test_partial_history_names_the_missing_timeframe(session):
    """Дневки есть, часовых нет — сказано именно это."""
    inst = add_instrument(session)
    add_bars(session, inst.instrument_id, "1d", uptrend_daily(200),
             NOW - timedelta(days=200), timedelta(days=1))
    result = scan(session, now=NOW)
    assert len(result.skipped) == 1
    assert "часовых баров" in result.skipped[0].reason


def test_full_history_produces_idea_or_named_rejections(session):
    """С полной историей конвейер обязан дать либо идею, либо отказы с причиной.

    Промолчать он не может: иначе «сегодня нет сетапов» неотличимо от
    «движок сломался».
    """
    inst = add_instrument(session)
    add_bars(session, inst.instrument_id, "1d", uptrend_daily(200),
             NOW - timedelta(days=200), timedelta(days=1))
    add_bars(session, inst.instrument_id, "1h", uptrend_hourly(400),
             NOW - timedelta(hours=400), timedelta(hours=1))

    result = scan(session, now=NOW)
    assert result.scanned == 1
    assert result.produced > 0 or result.rejections, "конвейер промолчал"
    for rejection in result.rejections:
        assert rejection.reason, "отказ без причины"
        assert rejection.failed, "отказ без проваленной проверки"


def test_every_rejection_names_a_failed_check(session):
    """Каждый отказ ссылается на конкретное условие ТЗ."""
    inst = add_instrument(session)
    add_bars(session, inst.instrument_id, "1d", uptrend_daily(200),
             NOW - timedelta(days=200), timedelta(days=1))
    add_bars(session, inst.instrument_id, "1h", uptrend_hourly(400),
             NOW - timedelta(hours=400), timedelta(hours=1))
    result = scan(session, now=NOW)
    for rejection in result.rejections:
        names = {c.name for c in rejection.failed}
        assert names, rejection.strategy
        assert all(c.detail for c in rejection.failed)


def test_broken_instrument_does_not_kill_the_scan(session):
    """Отказ на одном инструменте не должен ронять весь прогон.

    Иначе одна кривая бумага лишает владельца всей выдачи дня.
    """
    good = add_instrument(session, "MOEX:FUT:GOOD")
    add_bars(session, good.instrument_id, "1d", uptrend_daily(200),
             NOW - timedelta(days=200), timedelta(days=1))
    add_bars(session, good.instrument_id, "1h", uptrend_hourly(400),
             NOW - timedelta(hours=400), timedelta(hours=1))
    add_instrument(session, "MOEX:FUT:EMPTY")

    result = scan(session, now=NOW)
    assert result.scanned == 2
    assert any(s.instrument_id == "MOEX:FUT:EMPTY" for s in result.skipped)


# ─── Запись в журнал ──────────────────────────────────────────────────────


def test_saved_idea_gets_initial_event(session):
    """§18: у каждой идеи есть первое событие с самого рождения."""
    inst = add_instrument(session)
    add_bars(session, inst.instrument_id, "1d", uptrend_daily(200),
             NOW - timedelta(days=200), timedelta(days=1))
    add_bars(session, inst.instrument_id, "1h", uptrend_hourly(400),
             NOW - timedelta(hours=400), timedelta(hours=1))

    result = scan(session, now=NOW)
    for idea in result.ideas:
        events = session.execute(
            select(IdeaEvent).where(IdeaEvent.idea_id == idea.id)
        ).scalars().all()
        assert len(events) == 1
        assert events[0].sequence == 1
        assert events[0].reason_code == "scan"
        assert events[0].config_hash == idea.config_hash


def test_idea_carries_config_hash_and_versions(session):
    """§27: по отпечатку видно, при каких числах получена идея."""
    inst = add_instrument(session)
    add_bars(session, inst.instrument_id, "1d", uptrend_daily(200),
             NOW - timedelta(days=200), timedelta(days=1))
    add_bars(session, inst.instrument_id, "1h", uptrend_hourly(400),
             NOW - timedelta(hours=400), timedelta(hours=1))
    result = scan(session, now=NOW)
    for idea in result.ideas:
        assert len(idea.config_hash) == 64
        assert idea.engine_version and idea.feature_version


def test_score_breakdown_is_stored_with_every_component(session):
    """Разбор оценки хранится целиком: спорить можно только с числами."""
    inst = add_instrument(session)
    add_bars(session, inst.instrument_id, "1d", uptrend_daily(200),
             NOW - timedelta(days=200), timedelta(days=1))
    add_bars(session, inst.instrument_id, "1h", uptrend_hourly(400),
             NOW - timedelta(hours=400), timedelta(hours=1))
    result = scan(session, now=NOW)
    for idea in result.ideas:
        breakdown = idea.explanation_json["score_breakdown"]
        assert len(breakdown) == 11
        assert all("points" in c and "measured" in c for c in breakdown)


def test_not_presented_ideas_are_still_saved(session):
    """§12: хранится любая идея, даже не попавшая в три карточки."""
    for k in range(5):
        inst = add_instrument(session, f"MOEX:FUT:T{k}")
        add_bars(session, inst.instrument_id, "1d", uptrend_daily(200),
                 NOW - timedelta(days=200), timedelta(days=1))
        add_bars(session, inst.instrument_id, "1h", uptrend_hourly(400),
                 NOW - timedelta(hours=400), timedelta(hours=1))

    result = scan(session, now=NOW)
    stored = session.execute(select(TradeIdea)).scalars().all()
    assert len(stored) == result.produced
    if result.daily is not None:
        shown = len(result.daily.trade_now) + len(result.daily.wait_for_trigger)
        assert shown <= 3


def test_idea_without_tradable_size_stays_watch(session):
    """§20.1: объём меньше лота — идея информационная, не сработавшая."""
    inst = add_instrument(session)
    add_bars(session, inst.instrument_id, "1d", uptrend_daily(200),
             NOW - timedelta(days=200), timedelta(days=1))
    add_bars(session, inst.instrument_id, "1h", uptrend_hourly(400),
             NOW - timedelta(hours=400), timedelta(hours=1))

    # Мизерный капитал: бюджета не хватит даже на один контракт.
    result = scan(session, risk_state=RiskState(risk_equity=Decimal(100)), now=NOW)
    for idea in result.ideas:
        if idea.quantity == 0:
            assert idea.status == IdeaStatus.WATCH


def test_scan_is_deterministic(session):
    """§0.3: одни и те же бары дают один и тот же ответ.

    Недетерминированный движок невозможно ни отладить, ни оспорить.
    """
    inst = add_instrument(session)
    add_bars(session, inst.instrument_id, "1d", uptrend_daily(200),
             NOW - timedelta(days=200), timedelta(days=1))
    add_bars(session, inst.instrument_id, "1h", uptrend_hourly(400),
             NOW - timedelta(hours=400), timedelta(hours=1))

    first, _, _ = scan_instrument(
        session, inst, cfg=None or __import__("app.config", fromlist=["get_config"]).get_config(),
        risk_state=RiskState(risk_equity=Decimal(100_000)), now=NOW,
    )
    second, _, _ = scan_instrument(
        session, inst, cfg=__import__("app.config", fromlist=["get_config"]).get_config(),
        risk_state=RiskState(risk_equity=Decimal(100_000)), now=NOW,
    )
    if first is None or second is None:
        assert first is None and second is None
    else:
        assert first.score == second.score
        assert first.entry_reference == second.entry_reference
        assert first.stop == second.stop
        assert first.p_tp1_before_sl == second.p_tp1_before_sl


# ── Повторный скан не плодит идей ───────────────────────────────────────────


def _идея(session, instrument_id: str, **overrides) -> TradeIdea:
    from tests.conftest import idea_kwargs

    base = idea_kwargs(instrument_id, NOW)
    base.update(overrides)
    return TradeIdea(**base)


def test_повторный_скан_не_создаёт_вторую_идею(session, monkeypatch):
    """Экран владельца: десять позиций по HYPEUSDT подряд.

    Скан идёт каждые пятнадцать минут и находит один и тот же сетап. Раньше
    каждая находка записывалась отдельной идеей, и по каждой открывалась
    отдельная бумажная сделка: входы 52,2860 … 52,2950 — одна сделка,
    пересчитанная десять раз.

    Конвейер здесь подменён намеренно: проверяется страж в `scan()`, а не
    то, даёт ли сетап конкретный набор баров. Иначе тест молча
    превращается в пропуск ровно тогда, когда рынок спокоен.
    """
    inst = add_instrument(session)
    import app.pipeline.scan as scan_module

    monkeypatch.setattr(
        scan_module,
        "scan_instrument",
        lambda session, instrument, **kw: (
            _идея(session, instrument.instrument_id), [], []
        ),
    )

    первый = scan(session, now=NOW)
    assert первый.produced == 1

    второй = scan(session, now=NOW + timedelta(minutes=15))

    assert второй.produced == 0
    дубли = [s for s in второй.skipped if s.stage == "дубль"]
    assert дубли, "повтор пропал молча"
    assert "уже есть живая идея" in дубли[0].reason
    живых = session.execute(
        select(TradeIdea).where(TradeIdea.instrument_id == inst.instrument_id)
    ).scalars().all()
    assert len(живых) == 1


def test_терминальная_идея_новой_не_мешает(session, monkeypatch):
    """Сетап действительно может появиться снова — после того как прошлый
    отработал, был обогнан рынком или протух."""
    inst = add_instrument(session)
    import app.pipeline.scan as scan_module

    monkeypatch.setattr(
        scan_module,
        "scan_instrument",
        lambda session, instrument, **kw: (
            _идея(session, instrument.instrument_id), [], []
        ),
    )

    первый = scan(session, now=NOW)
    for idea in первый.ideas:
        idea.status = IdeaStatus.CLOSED
    session.flush()

    второй = scan(session, now=NOW + timedelta(minutes=15))

    assert второй.produced == 1
    assert not [s for s in второй.skipped if s.stage == "дубль"]


def test_один_проход_не_плодит_дублей_внутри_себя(session, monkeypatch):
    """Два инструмента — две идеи; повтор внутри прохода тоже ловится."""
    add_instrument(session)
    import app.pipeline.scan as scan_module

    monkeypatch.setattr(
        scan_module,
        "scan_instrument",
        lambda session, instrument, **kw: (
            _идея(session, instrument.instrument_id), [], []
        ),
    )

    первый = scan(session, now=NOW)
    assert первый.produced == 1
    # Тот же проход ещё раз, без изменения статуса: идея уже живая.
    assert scan(session, now=NOW).produced == 0
