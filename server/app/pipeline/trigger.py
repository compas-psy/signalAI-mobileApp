"""Перепроверка триггера у наблюдаемых идей (engine-ТЗ §11, §15.6).

Идея рождается один раз и оценивается по тем барам, что были на момент
рождения. Ворот допуска §15.6 семь, и одни из них по своей природе
неизменны — вероятность, ожидание, соотношение риска считаются от плана и
новых баров не ждут, — а одни изменчивы по построению. Таких ровно одно:
подтверждение на триггерном таймфрейме. Его может не быть в 22:04 и быть в
01:00, потому что паттерн формируется свечами.

Пока этого шага не существовало, состояние ``WATCH`` было тупиком. Идея,
которой не хватило **только** триггера, оставалась наблюдением навсегда:
скан больше её не трогал, супервизор отказывался повышать статус
намеренно — и правильно, — а другого места не было. Цена доходила до зоны
входа, карточка отвечала «подтверждение доступно только у сработавшего
сигнала», и сработать сигнал не мог ни при каких обстоятельствах.

Здесь триггер именно **перепроверяется**, а не выводится из касания зоны.
Работает тот же детектор ``price_action`` на тех же часовых свечах, с той
же ролью контекстной зоны — только зона берётся из самой идеи, из её
диапазона входа. Это и есть §11: подтверждение на своём таймфрейме внутри
зоны. Разница с «цена коснулась зоны» принципиальная: касание бывает
случайным проколом, а паттерн — это форма свечей, которую детектор либо
находит, либо нет.

Перепроверка берёт идеи, среди непройденных ворот которых есть триггер, и
после подтверждения **перепрогоняет остальные ворота**. Прежнее условие
«триггер был единственным непройденным» выглядело строгим, а на деле было
недостижимым: ворота уверенности не проходил никто, поэтому в списке
всегда лежало минимум два имени и не повышалось ничего никогда.

Идее, которой не хватило вероятности, подтверждение по-прежнему ничего не
даёт: качество от новых свечей не улучшается. Но теперь это видно —
причина называется отдельной строкой, а не прячется за молчанием.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..detectors import price_action
from ..detectors.price_action import PriceZone
from ..journal.lifecycle import TransitionRequest, transition
from ..models import Bar, TradeIdea
from ..models.enums import Direction, IdeaStatus, QualityStatus, Timeframe

# Состояния, в которых перепроверка имеет смысл: план жив, сделки нет.
PENDING: frozenset[IdeaStatus] = frozenset(
    {IdeaStatus.DISCOVERED, IdeaStatus.WATCH}
)

# Тот же таймфрейм, что у скана: роль триггера задана §7 и здесь не меняется.
TRIGGER_TF = Timeframe.H1

# Имя ворот §15.6, отвечающих за подтверждение.
TRIGGER_GATE = "trigger"

# Сколько часовых свечей подавать детектору.
#
# Ему нужен ATR за 14 периодов плюс сами свечи паттерна; двести дают запас
# и не превращают перепроверку в загрузку истории.
WINDOW = 200


@dataclass
class TriggerReport:
    checked: int = 0
    promoted: int = 0
    no_data: int = 0
    #: Кто именно остался без баров. Планировщик печатает эти имена, и
    #: поля здесь не было: строка отчёта писалась по образцу супервизора,
    #: где оно есть. При первом же инструменте без баров задача падала с
    #: AttributeError, а `Scheduler.tick` откатывал транзакцию — вместе с
    #: подтверждениями, которые в этом же прогоне уже прошли.
    no_data_instruments: list[str] = field(default_factory=list)
    not_eligible: int = 0
    #: Триггер подтвердился, но идея не проходит другие ворота §15.6.
    #: Отдельно от `promoted`: «подтвердилось и повысили» и «подтвердилось,
    #: но качества всё ещё не хватает» — разные новости.
    confirmed_but_blocked: int = 0
    details: list[str] = field(default_factory=list)


def eligible(idea: TradeIdea) -> bool:
    """Можно ли этой идее добавить подтверждения.

    Достаточно того, что среди непройденных ворот есть триггер. Раньше
    здесь стояло «триггер был **единственным** непройденным воротом», и
    это оказалось недостижимым условием: ворота уверенности не мог пройти
    никто, поэтому в списке всегда лежало как минимум два имени, и
    перепроверка не повышала ничего никогда.

    Требовать единственности и не нужно. Подтверждение триггера — новый
    факт, а не разрешение: после него допуск перепрогоняется целиком
    (`still_blocked`), и идея, которой не хватает вероятности или
    ожидания, останется наблюдением с названной причиной.

    Список ворот пишет скан; у идей, созданных до его появления, списка
    нет, и они пропускаются — угадывать причину задним числом хуже, чем
    не трогать.
    """
    failed = (idea.explanation_json or {}).get("admission_failed")
    if not isinstance(failed, list) or not failed:
        return False
    return TRIGGER_GATE in failed


def still_blocked(idea: TradeIdea) -> list[str]:
    """Что мешает повышению, кроме уже подтверждённого триггера.

    Повышать вслепую нельзя: триггер — только одно из ворот §15.6.
    Возвращается список оставшихся отказов, и пустой список означает, что
    идее не хватало ровно подтверждения.
    """
    failed = (idea.explanation_json or {}).get("admission_failed") or []
    return [gate for gate in failed if gate != TRIGGER_GATE]


def confirmed(reading) -> bool:
    """Достаточно ли этой находки, чтобы повысить статус идеи.

    Детектор отвечает не «да/нет», а «вот что нашлось и насколько это
    хорошо»: он возвращает лучший из кандидатов вместе со списком проверок,
    среди которых есть и «в контекстной зоне». Паттерн вне зоны приезжает
    оттуда с честной пометкой «вне контекстной зоны — паттерн не в счёт», но
    приезжает.

    Для оценки идеи этого хватает: там находка идёт в компонент качества со
    своим весом. Для повышения статуса — нет. ``pa_reading is not None``
    здесь означало бы, что любое поглощение на графике, где бы оно ни
    случилось, объявляет сигнал сработавшим. Поэтому спрашивается пройденность
    всех проверок паттерна, а не факт его существования.
    """
    if reading is None:
        return False
    trigger = reading.payload.get("trigger")
    return bool(trigger is not None and trigger.passed)


def _zone(idea: TradeIdea) -> PriceZone:
    """Зона входа идеи как контекст для детектора.

    Детектор без зоны не работает вовсе (§10.4: «паттерн не считается
    самостоятельным сигналом»), и зона здесь не выдумывается: это тот самый
    диапазон, в котором стоит лимитка.
    """
    low, high = idea.entry_low, idea.entry_high
    if low > high:
        low, high = high, low
    return PriceZone(low, high, "entry")


def _candles(session: Session, idea: TradeIdea, *, limit: int = WINDOW):
    from .scan import _load_bars

    return _load_bars(session, idea.instrument_id, TRIGGER_TF, limit)


def mark_confirmed(idea: TradeIdea, summary: str) -> None:
    """Синхронизировать семантику качества после отложенного триггера.

    WATCH был качественным результатом именно потому, что не хватало
    рыночного подтверждения. Если это был последний непройденный gate и тот
    же детектор позже его подтвердил, идея уже ACTIVE по тем же правилам
    §15.6. Оставлять ``quality_status=WATCH`` рядом с ``status=TRIGGERED``
    означало хранить два взаимоисключающих ответа и блокировать approve-paper.

    Никакой балл/вероятность/стратегия здесь не пересчитываются: меняется
    только факт прохождения уже существующего trigger gate.
    """
    idea.quality_status = QualityStatus.ACTIVE
    explanation = dict(idea.explanation_json or {})
    explanation["admission_failed"] = []
    explanation["admission"] = "все условия §15.6 выполнены после подтверждения триггера"
    timeframes = [dict(row) for row in explanation.get("timeframes", [])]
    for row in timeframes:
        if row.get("role") == "trigger" or row.get("tf") == "1H":
            row["summary"] = summary
    if timeframes:
        explanation["timeframes"] = timeframes
    idea.explanation_json = explanation


def recheck(
    session: Session, *, now: datetime | None = None, limit: int = 500
) -> TriggerReport:
    """Пройти по наблюдаемым идеям и подтвердить те, где триггер появился."""
    moment = now or datetime.now(UTC)
    report = TriggerReport()

    ideas = list(
        session.execute(
            select(TradeIdea)
            .where(TradeIdea.status.in_([s.value for s in PENDING]))
            .order_by(TradeIdea.signal_time.desc())
            .limit(limit)
        ).scalars()
    )

    for idea in ideas:
        report.checked += 1
        if not eligible(idea):
            report.not_eligible += 1
            continue

        candles = _candles(session, idea)
        if not candles:
            report.no_data += 1
            if idea.instrument_id not in report.no_data_instruments:
                report.no_data_instruments.append(idea.instrument_id)
            continue

        reading = price_action.detect(
            candles,
            timeframe="1h",
            zones=[_zone(idea)],
            higher_tf_direction=(
                "long" if idea.direction is Direction.LONG else "short"
            ),
        )
        if not confirmed(reading):
            continue

        blocked = still_blocked(idea)
        if blocked:
            # Подтверждение пришло, но допуск идея всё равно не проходит.
            # Это не повод повышать: триггер — одно из ворот, а не пропуск
            # мимо остальных. Причина называется, чтобы «подтвердилось, а
            # ничего не изменилось» не выглядело поломкой.
            report.confirmed_but_blocked += 1
            report.details.append(
                f"{idea.instrument_id}: триггер подтверждён, "
                f"но не пройдено: {', '.join(blocked)}"
            )
            continue

        mark_confirmed(idea, reading.summary)
        transition(
            session,
            idea,
            TransitionRequest(
                new_status=IdeaStatus.TRIGGERED,
                reason_code="trigger_confirmed",
                reason_detail=(
                    f"подтверждение на 1h внутри зоны входа: {reading.summary}"
                )[:512],
                market_snapshot={
                    "bars_checked": len(candles),
                    "last_close": str(candles[-1].close),
                    "checked_at": moment.isoformat(),
                },
            ),
        )
        report.promoted += 1
        report.details.append(f"{idea.instrument_id}: {reading.summary}")

    return report


__all__ = [
    "confirmed",
    "mark_confirmed",
    "still_blocked",
    "PENDING",
    "TRIGGER_GATE",
    "TRIGGER_TF",
    "TriggerReport",
    "eligible",
    "recheck",
]
