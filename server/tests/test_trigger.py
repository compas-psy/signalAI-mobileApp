"""Перепроверка триггера у наблюдаемых идей §11, §15.6.

Владелец увидел идею, цена по которой дошла до точки входа, и спросил:
почему ничего не произошло. Ответ был неприятный: состояние ``WATCH`` было
тупиком. Идея, которой не хватило только подтверждения на триггерном
таймфрейме, оставалась наблюдением навсегда — скан её больше не трогал,
супервизор отказывался повышать статус намеренно, а третьего места не было.
Карточка отвечала «подтверждение доступно только у сработавшего сигнала», и
сработать сигнал не мог ни при каких обстоятельствах.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Bar, IdeaEvent, TradeIdea
from app.models.enums import IdeaStatus, Timeframe
from app.pipeline.trigger import eligible, recheck
from tests.conftest import idea_kwargs

NOW = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)


def watch_idea(instrument_id: str, failed: list[str] | None, **overrides) -> TradeIdea:
    """Наблюдение с зоной входа 90000–90200 и целями вниз.

    ``failed`` — какие ворота §15.6 не прошли. None означает идею, созданную
    до появления списка: такие не перепроверяются.
    """
    base = idea_kwargs(instrument_id, NOW)
    explanation = dict(base.get("explanation_json") or {})
    if failed is not None:
        explanation["admission_failed"] = failed
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
        explanation_json=explanation,
    )
    base.update(overrides)
    return TradeIdea(**base)


def _bars(instrument_id: str, *, floor: Decimal | None = None) -> list[Bar]:
    """Часовой ряд, на котором триггер действительно формируется.

    Форма именно такая, потому что детектор ищет паттерн, а не касание:
    ряду нужна предыстория для ATR, подход к зоне сверху и свечи разворота
    внутри неё. Тест, где паттерна нет, доказывал бы только то, что модуль
    ничего не делает.

    ``floor`` поднимает весь ряд выше зоны, сохраняя форму: так проверяется,
    что подтверждает именно зона, а не форма свечей сама по себе.
    """
    rows: list[Bar] = []
    price = Decimal("92000")

    def add(index: int, o, h, l, c, volume="1000") -> None:
        rows.append(
            Bar(
                instrument_id=instrument_id,
                timeframe=Timeframe.H1,
                open_time=NOW + timedelta(hours=index + 1),
                open=Decimal(str(o)),
                high=Decimal(str(h)),
                low=Decimal(str(l)),
                close=Decimal(str(c)),
                volume_units=Decimal(volume),
                is_closed=True,
                source="test",
            )
        )

    shift = (floor - Decimal("90000")) if floor is not None else Decimal(0)
    for i in range(40):
        price -= Decimal("40")
        add(i, price + 30 + shift, price + 60 + shift, price - 30 + shift, price + shift)
    # Подход в зону, ложный вынос вверх и разворот вниз на объёме.
    add(40, 90100 + shift, 90180 + shift, 90050 + shift, 90120 + shift)
    add(41, 90120 + shift, 90600 + shift, 90090 + shift, 90130 + shift, volume="4000")
    add(42, 90130 + shift, 90150 + shift, 89600 + shift, 89650 + shift, volume="5000")
    return rows


def test_идея_без_списка_ворот_не_перепроверяется(instrument):
    """Угадывать причину задним числом хуже, чем не трогать.

    Идеи, созданные до появления списка, его не получат: журнал только
    дополняется.
    """
    assert not eligible(watch_idea(instrument.instrument_id, None))


def test_перепроверяется_только_нехватка_триггера(instrument):
    iid = instrument.instrument_id
    assert eligible(watch_idea(iid, ["trigger"]))
    # Вероятность новыми свечами не улучшается: делать вид, что улучшилась,
    # значит подменить порог допуска ожиданием.
    assert not eligible(watch_idea(iid, ["probability"]))
    assert not eligible(watch_idea(iid, ["trigger", "expected_r"]))


def test_без_баров_статус_не_меняется(session: Session, instrument):
    session.add(watch_idea(instrument.instrument_id, ["trigger"]))
    session.flush()

    report = recheck(session, now=NOW + timedelta(days=1))

    assert report.checked == 1
    assert report.no_data == 1
    assert report.promoted == 0


def test_идея_вне_перепроверки_считается_отдельно(session: Session, instrument):
    session.add(watch_idea(instrument.instrument_id, ["probability"]))
    for row in _bars(instrument.instrument_id):
        session.add(row)
    session.flush()

    report = recheck(session, now=NOW + timedelta(days=1))

    assert report.not_eligible == 1
    assert report.promoted == 0
    # Идея осталась наблюдением: качество перепроверкой не лечится.
    idea = session.query(TradeIdea).one()
    assert idea.status == IdeaStatus.WATCH


def test_подтверждение_переводит_идею_и_пишется_в_журнал(
    session: Session, instrument
):
    """Повышение — событие журнала, а не тихая правка поля.

    Без записи в журнал нельзя ответить, почему идея стала сработавшей, — а
    именно этот вопрос и задают, когда сделка не удалась.
    """
    session.add(watch_idea(instrument.instrument_id, ["trigger"]))
    for row in _bars(instrument.instrument_id):
        session.add(row)
    session.flush()

    report = recheck(session, now=NOW + timedelta(days=1))

    assert report.promoted == 1
    idea = session.query(TradeIdea).one()
    assert idea.status == IdeaStatus.TRIGGERED
    events = session.query(IdeaEvent).filter_by(idea_id=idea.id).all()
    assert any(e.reason_code == "trigger_confirmed" for e in events)


def test_за_пределами_зоны_подтверждения_не_бывает(session: Session, instrument):
    """Зона обязательна: §10.4 — паттерн не считается самостоятельным сигналом.

    Свечи разворота той же формы, но вне диапазона входа, не подтверждают
    ничего. Иначе подтверждением стал бы любой разворот на графике, где бы
    он ни случился.
    """
    session.add(watch_idea(instrument.instrument_id, ["trigger"]))
    for row in _bars(instrument.instrument_id, floor=Decimal("92000")):
        session.add(row)
    session.flush()

    report = recheck(session, now=NOW + timedelta(days=1))

    assert report.promoted == 0
    assert session.query(TradeIdea).one().status == IdeaStatus.WATCH


def test_сработавшая_идея_повторно_не_трогается(session: Session, instrument):
    session.add(
        watch_idea(
            instrument.instrument_id, ["trigger"], status=IdeaStatus.TRIGGERED
        )
    )
    for row in _bars(instrument.instrument_id):
        session.add(row)
    session.flush()

    report = recheck(session, now=NOW + timedelta(days=1))

    assert report.checked == 0
