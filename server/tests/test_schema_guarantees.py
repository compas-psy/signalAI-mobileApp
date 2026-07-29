"""Гарантии схемы: журнал не переписывается, план не может быть перевёрнут.

Это не тесты «объект сохранился». Это проверки того, что база **отказывает**
там, где ошибка стоила бы денег: перевёрнутый стоп, цели не по направлению,
правка истории. Каждое такое правило есть в ТЗ, и каждое здесь предъявлено.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models import AuditEvent, IdeaEvent, Instrument, TradeIdea
from app.models.enums import AssetClass, Venue
from tests.conftest import idea_kwargs


# ─── Журнал только дополняется (engine-ТЗ §18) ────────────────────────────


def _make_idea(session, instrument, now, **overrides) -> TradeIdea:
    idea = TradeIdea(**idea_kwargs(instrument.instrument_id, now, **overrides))
    session.add(idea)
    session.flush()
    return idea


def test_idea_event_cannot_be_updated(session, instrument, now):
    """Переход состояния нельзя переписать — даже владельцу базы."""
    idea = _make_idea(session, instrument, now)
    event = IdeaEvent(
        idea_id=idea.id,
        sequence=1,
        old_status=None,
        new_status="DISCOVERED",
        reason_code="scan",
        config_hash="0" * 64,
        engine_version="0.1.0",
    )
    session.add(event)
    session.flush()

    with pytest.raises(DBAPIError) as exc:
        session.execute(
            text("UPDATE idea_events SET reason_code = 'подделка' WHERE id = :id"),
            {"id": event.id},
        )
    assert "только дополняется" in str(exc.value)


def test_idea_event_cannot_be_deleted(session, instrument, now):
    """Удалить неудобную запись тоже нельзя."""
    idea = _make_idea(session, instrument, now)
    event = IdeaEvent(
        idea_id=idea.id,
        sequence=1,
        new_status="DISCOVERED",
        reason_code="scan",
        config_hash="0" * 64,
        engine_version="0.1.0",
    )
    session.add(event)
    session.flush()

    with pytest.raises(DBAPIError) as exc:
        session.execute(
            text("DELETE FROM idea_events WHERE id = :id"), {"id": event.id}
        )
    assert "только дополняется" in str(exc.value)


def test_audit_event_is_append_only(session):
    """Аудит действий с деньгами защищён тем же правилом (UX-ТЗ §23)."""
    event = AuditEvent(actor="owner", action="risk_limits_changed", detail="дневной 1.5%")
    session.add(event)
    session.flush()

    with pytest.raises(DBAPIError):
        session.execute(
            text("UPDATE audit_events SET actor = 'system' WHERE id = :id"),
            {"id": event.id},
        )


def test_new_events_are_still_allowed(session, instrument, now):
    """Запрет касается правки, а не записи: журнал обязан пополняться."""
    idea = _make_idea(session, instrument, now)
    for i in range(1, 4):
        session.add(
            IdeaEvent(
                idea_id=idea.id,
                sequence=i,
                new_status="WATCH",
                reason_code=f"step-{i}",
                config_hash="0" * 64,
                engine_version="0.1.0",
            )
        )
    session.flush()
    count = session.execute(
        text("SELECT count(*) FROM idea_events WHERE idea_id = :id"), {"id": idea.id}
    ).scalar_one()
    assert count == 3


def test_event_sequence_is_unique_per_idea(session, instrument, now):
    """Гонка двух воркеров не создаст два «шага 1» у одной идеи."""
    idea = _make_idea(session, instrument, now)
    common = dict(
        idea_id=idea.id,
        sequence=1,
        new_status="WATCH",
        reason_code="scan",
        config_hash="0" * 64,
        engine_version="0.1.0",
    )
    session.add(IdeaEvent(**common))
    session.flush()
    session.add(IdeaEvent(**common))
    with pytest.raises(IntegrityError):
        session.flush()


def test_idea_with_events_cannot_be_deleted(session, instrument, now):
    """Идею с историей не удалить: журнал не должен осиротеть."""
    idea = _make_idea(session, instrument, now)
    session.add(
        IdeaEvent(
            idea_id=idea.id,
            sequence=1,
            new_status="DISCOVERED",
            reason_code="scan",
            config_hash="0" * 64,
            engine_version="0.1.0",
        )
    )
    session.flush()
    with pytest.raises(IntegrityError):
        session.execute(text("DELETE FROM trade_ideas WHERE id = :id"), {"id": idea.id})
        session.flush()


# ─── План не может быть перевёрнут ────────────────────────────────────────


def test_long_stop_must_be_below_entry(session, instrument, now):
    """Лонг со стопом выше входа — перевёрнутый знак риска, а не «странный план»."""
    with pytest.raises(IntegrityError) as exc:
        _make_idea(session, instrument, now, stop=Decimal("91000"))
    assert "stop_on_correct_side" in str(exc.value)


def test_short_stop_must_be_above_entry(session, instrument, now):
    with pytest.raises(IntegrityError) as exc:
        _make_idea(
            session,
            instrument,
            now,
            direction="SHORT",
            stop=Decimal("89000"),
            tp1=Decimal("89000"),
            tp2=Decimal("88000"),
            tp3=Decimal("87000"),
        )
    assert "stop_on_correct_side" in str(exc.value)


def test_short_plan_is_valid_when_mirrored(session, instrument, now):
    """Зеркальный шорт проходит: ограничение ловит ошибку, а не направление."""
    idea = _make_idea(
        session,
        instrument,
        now,
        direction="SHORT",
        stop=Decimal("90800"),
        tp1=Decimal("89000"),
        tp2=Decimal("88000"),
        tp3=Decimal("87000"),
    )
    assert idea.direction == "SHORT"


def test_targets_must_go_in_trade_direction(session, instrument, now):
    """TP2 ближе TP1 — цели переставлены местами, план исполнять нельзя."""
    with pytest.raises(IntegrityError) as exc:
        _make_idea(session, instrument, now, tp1=Decimal("92000"), tp2=Decimal("91000"))
    assert "targets_ordered_by_direction" in str(exc.value)


def test_probability_must_be_a_probability(session, instrument, now):
    """1.4 — не вероятность. §7: p строго определена и лежит в [0, 1]."""
    with pytest.raises(IntegrityError) as exc:
        _make_idea(session, instrument, now, p_tp1_before_sl=Decimal("1.4"))
    assert "p_tp1_is_probability" in str(exc.value)


def test_expiry_must_follow_signal(session, instrument, now):
    """Идея, истёкшая до появления, — сломанные часы, а не короткий сигнал."""
    with pytest.raises(IntegrityError) as exc:
        _make_idea(session, instrument, now, expires_at=now - timedelta(hours=1))
    assert "expiry_after_signal" in str(exc.value)


# ─── Точность денег (UX-ТЗ §17.1) ─────────────────────────────────────────


def test_decimal_survives_roundtrip(session, instrument, now):
    """Риск 0.005 обязан вернуться как 0.005, а не как 0.004999999999999999.

    Размер позиции считается делением бюджета риска на разницу цен. Двоичный
    хвост здесь превращается в лишний контракт.
    """
    idea = _make_idea(
        session,
        instrument,
        now,
        risk_pct=Decimal("0.005"),
        risk_amount=Decimal("1234.56789012"),
        entry_reference=Decimal("90123.456789012345"),
    )
    session.expire(idea)
    stored = session.get(TradeIdea, idea.id)

    assert stored.risk_pct == Decimal("0.005")
    assert stored.risk_amount == Decimal("1234.56789012")
    assert stored.entry_reference == Decimal("90123.456789012345")
    assert isinstance(stored.risk_amount, Decimal)


# ─── Перечисления переживают базу ─────────────────────────────────────────


def test_enum_survives_roundtrip(session, instrument):
    """Из базы обязан вернуться член перечисления, а не строка.

    Дефект не гипотетический. Пока колонка была обычным ``String``, проверка
    ``instrument.venue is Venue.MOEX`` всегда была ложной, и фьючерсы MOEX
    молча уходили загружаться на криптобиржу: запрос уходил на
    ``api.bybit.com`` с символом вроде ``SIU6``, отвечал пустотой, а отчёт
    показывал «загружено 0» без единой ошибки. Такое стоит проверять на
    самом дешёвом уровне — на типе колонки.
    """
    session.expire(instrument)
    stored = session.execute(
        select(Instrument).where(Instrument.instrument_id == "MOEX:FUT:SIU6")
    ).scalar_one()

    assert stored.venue is Venue.MOEX
    assert stored.asset_class is AssetClass.FUTURES
    assert stored.venue.value == "MOEX"


def test_enum_column_stores_readable_text(session, instrument):
    """В базе остаётся человекочитаемая строка, а не номер варианта.

    Журнал читается глазами и запросами из psql; числовой код варианта
    сделал бы дамп нечитаемым, а добавление нового значения — миграцией.
    """
    value = session.execute(
        text(
            "SELECT venue FROM instruments WHERE instrument_id = :i"
        ),
        {"i": "MOEX:FUT:SIU6"},
    ).scalar_one()
    assert value == "MOEX"


def test_bar_high_cannot_be_below_low(session, instrument):
    """Перепутанные high/low ломают все уровни — база их не примет."""
    from datetime import UTC, datetime

    from app.models import Bar

    session.add(
        Bar(
            instrument_id=instrument.instrument_id,
            timeframe="1h",
            open_time=datetime(2026, 7, 29, 9, tzinfo=UTC),
            open=Decimal("100"),
            high=Decimal("99"),
            low=Decimal("101"),
            close=Decimal("100"),
            source="moex",
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
