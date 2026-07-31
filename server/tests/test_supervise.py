"""Сопровождение живых идей §9, §18.

Вопрос, ради которого этот модуль существует, задал владелец, глядя на
карточку BTCUSDT: цена уже у третьей цели, а идея по-прежнему «Watch,
сигнал живёт 4 дн 6 ч». Актуальна она или нет — ответить было нечем.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Bar, IdeaEvent, TradeIdea
from app.models.enums import IdeaStatus, Timeframe
from app.pipeline.supervise import judge, supervise
from tests.conftest import idea_kwargs

NOW = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)


def bar(instrument_id: str, at: datetime, low, high, close=None) -> Bar:
    """Час рынка. Границы важнее закрытия: зону задевает тень."""
    low = Decimal(str(low))
    high = Decimal(str(high))
    close = Decimal(str(close)) if close is not None else (low + high) / 2
    return Bar(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        open_time=at,
        open=close,
        high=high,
        low=low,
        close=close,
        is_closed=True,
        source="test",
    )


def short_idea(instrument_id: str, **overrides) -> TradeIdea:
    """Шорт: зона 90000–90200, стоп 90800, цели вниз до 88000.

    Числа как у настоящей идеи: цели по направлению сделки и стоп за зоной,
    иначе ограничения таблицы отклонят запись раньше, чем начнётся проверка.
    """
    base = idea_kwargs(instrument_id, NOW)
    base.update(
        direction="SHORT",
        status=IdeaStatus.WATCH,
        entry_low=Decimal("90000"),
        entry_high=Decimal("90200"),
        entry_reference=Decimal("90100"),
        stop=Decimal("90800"),
        tp1=Decimal("89400"),
        tp2=Decimal("88800"),
        tp3=Decimal("88000"),
    )
    base.update(overrides)
    return TradeIdea(**base)


# ─── Приговор по барам ────────────────────────────────────────────────────


def test_price_reaching_the_far_target_without_us_is_missed(instrument):
    """Тот самый BTCUSDT: цена у TP3, зону входа не трогали."""
    idea = short_idea(instrument.instrument_id)
    bars = [
        bar(instrument.instrument_id, NOW + timedelta(hours=1), 89500, 89900),
        bar(instrument.instrument_id, NOW + timedelta(hours=2), 87900, 88500),
    ]
    verdict = judge(idea, bars, now=NOW + timedelta(hours=3))
    assert verdict.status is IdeaStatus.MISSED
    assert "не зайдя в зону входа" in verdict.detail


def test_price_through_the_stop_before_entry_is_a_broken_idea(instrument):
    """Прошли стоп до входа — это не «поздний вход», а другая картина."""
    idea = short_idea(instrument.instrument_id)
    bars = [
        bar(instrument.instrument_id, NOW + timedelta(hours=1), 90400, 91000),
    ]
    verdict = judge(idea, bars, now=NOW + timedelta(hours=2))
    assert verdict.status is IdeaStatus.CANCELLED
    assert "замысел сломан" in verdict.detail


def test_touching_the_zone_leaves_the_idea_alone(instrument):
    """Вход был возможен — дальше это сопровождение сделки, не наш вопрос."""
    idea = short_idea(instrument.instrument_id)
    bars = [
        # Тень задела зону: лимитка исполняется тенью, а не телом.
        bar(instrument.instrument_id, NOW + timedelta(hours=1), 89500, 90050, close=89600),
        bar(instrument.instrument_id, NOW + timedelta(hours=2), 87900, 88500),
    ]
    assert judge(idea, bars, now=NOW + timedelta(hours=3)).status is None


def test_what_happened_first_wins_over_the_clock(instrument):
    """Идею, которую рынок обогнал во вторник, нельзя закрыть «по сроку».

    Иначе единственной причиной в журнале навсегда стало бы «срок вышел», и
    статистика пропущенных сетапов перестала бы существовать.
    """
    idea = short_idea(instrument.instrument_id)
    bars = [bar(instrument.instrument_id, NOW + timedelta(hours=1), 87900, 88500)]
    verdict = judge(idea, bars, now=NOW + timedelta(days=9))
    assert verdict.status is IdeaStatus.MISSED


def test_expiry_closes_an_idea_the_market_ignored(instrument):
    """Цена никуда не пошла, срок вышел — это тайм-аут, и так и написано."""
    idea = short_idea(instrument.instrument_id)
    bars = [bar(instrument.instrument_id, NOW + timedelta(hours=1), 90300, 90500)]
    verdict = judge(idea, bars, now=NOW + timedelta(days=6))
    assert verdict.status is IdeaStatus.TIMED_OUT
    assert "в зону входа так и не пришла" in verdict.detail


def test_live_idea_within_its_term_is_not_touched(instrument):
    idea = short_idea(instrument.instrument_id)
    bars = [bar(instrument.instrument_id, NOW + timedelta(hours=1), 90300, 90500)]
    assert judge(idea, bars, now=NOW + timedelta(hours=2)).status is None


def test_long_idea_is_judged_by_its_own_direction(instrument):
    """У лонга «дальняя цель» — максимум, а не минимум."""
    idea = short_idea(
        instrument.instrument_id,
        direction="LONG",
        entry_low=Decimal("89000"),
        entry_high=Decimal("89200"),
        entry_reference=Decimal("89100"),
        stop=Decimal("88400"),
        tp1=Decimal("89800"),
        tp2=Decimal("90400"),
        tp3=Decimal("91000"),
    )
    bars = [bar(instrument.instrument_id, NOW + timedelta(hours=1), 89500, 91200)]
    assert judge(idea, bars, now=NOW + timedelta(hours=2)).status is IdeaStatus.MISSED


# ─── Проход по базе ───────────────────────────────────────────────────────


def test_supervisor_writes_the_transition_into_the_journal(
    session: Session, instrument
):
    idea = short_idea(instrument.instrument_id)
    session.add(idea)
    session.flush()
    for hours, (low, high) in enumerate([(89500, 89900), (87900, 88500)], start=1):
        session.add(bar(instrument.instrument_id, NOW + timedelta(hours=hours), low, high))
    session.flush()

    report = supervise(session, now=NOW + timedelta(hours=3))
    assert report.checked == 1
    assert report.missed == 1
    assert idea.status is IdeaStatus.MISSED

    events = list(
        session.execute(
            IdeaEvent.__table__.select().where(IdeaEvent.idea_id == idea.id)
        )
    )
    assert len(events) == 1
    # Причина едет вместе с переходом: «MISSED» без объяснения в журнале
    # ничем не лучше молчания.
    assert events[0].reason_code == "price_left_without_entry"
    assert "зону входа" in events[0].reason_detail


def test_absent_bars_are_not_a_reason_to_close_an_idea(
    session: Session, instrument
):
    """Отказ загрузки — не событие рынка, и идею по нему хоронить нельзя."""
    # Срок вышел — но баров нет, и сказать по ним нечего.
    idea = short_idea(
        instrument.instrument_id,
        signal_time=NOW - timedelta(days=6),
        expires_at=NOW - timedelta(hours=1),
    )
    session.add(idea)
    session.flush()

    report = supervise(session, now=NOW)
    assert report.no_data == 1
    assert report.changed == 0
    assert IdeaStatus(idea.status) is IdeaStatus.WATCH


def test_terminal_ideas_are_not_revisited(session: Session, instrument):
    idea = short_idea(instrument.instrument_id, status=IdeaStatus.CLOSED)
    session.add(idea)
    session.add(bar(instrument.instrument_id, NOW + timedelta(hours=1), 87900, 88500))
    session.flush()

    report = supervise(session, now=NOW + timedelta(hours=2))
    assert report.checked == 0
    assert IdeaStatus(idea.status) is IdeaStatus.CLOSED


def test_triggered_idea_can_also_be_overtaken(session: Session, instrument):
    """Сработавший, но неподтверждённый сигнал рынок обгоняет так же."""
    idea = short_idea(instrument.instrument_id, status=IdeaStatus.TRIGGERED)
    session.add(idea)
    session.add(bar(instrument.instrument_id, NOW + timedelta(hours=1), 87900, 88500))
    session.flush()

    supervise(session, now=NOW + timedelta(hours=2))
    assert idea.status is IdeaStatus.MISSED
