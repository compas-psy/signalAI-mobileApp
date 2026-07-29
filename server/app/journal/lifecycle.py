"""Жизненный цикл идеи (engine-ТЗ §18).

Разрешённые переходы заданы таблицей и проверяются на записи. Переход,
которого нет в таблице, — не «странное состояние», а ошибка в логике; молча
принять его значит потерять способность доверять журналу.

Каждый переход пишется отдельной строкой в ``idea_events``. Таблица защищена
триггером базы от UPDATE и DELETE, поэтому историю нельзя ни подчистить, ни
переписать — ни из кода, ни руками через psql.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import IdeaEvent, TradeIdea
from ..models.enums import IdeaStatus
from ..version import ENGINE_VERSION

S = IdeaStatus

# Таблица переходов §18. Пустое множество — терминальное состояние.
ALLOWED: dict[IdeaStatus, frozenset[IdeaStatus]] = {
    S.DISCOVERED: frozenset({S.WATCH, S.TRIGGERED, S.RISK_BLOCKED, S.DATA_BLOCKED,
                             S.CANCELLED, S.MISSED}),
    S.WATCH: frozenset({S.TRIGGERED, S.CANCELLED, S.MISSED, S.TIMED_OUT,
                        S.RISK_BLOCKED, S.DATA_BLOCKED}),
    S.TRIGGERED: frozenset({S.ACTIVE, S.MISSED, S.CANCELLED, S.TIMED_OUT,
                            S.RISK_BLOCKED, S.DATA_BLOCKED}),
    S.ACTIVE: frozenset({S.PARTIALLY_FILLED, S.FILLED, S.CANCELLED, S.TIMED_OUT,
                         S.DATA_BLOCKED}),
    S.PARTIALLY_FILLED: frozenset({S.FILLED, S.TP1_HIT, S.STOPPED, S.CLOSED,
                                   S.DATA_BLOCKED}),
    S.FILLED: frozenset({S.TP1_HIT, S.STOPPED, S.MANAGING, S.TIMED_OUT, S.CLOSED,
                         S.DATA_BLOCKED}),
    S.TP1_HIT: frozenset({S.MANAGING, S.TP2_HIT, S.STOPPED, S.CLOSED}),
    S.MANAGING: frozenset({S.TP2_HIT, S.STOPPED, S.TIMED_OUT, S.CLOSED}),
    S.TP2_HIT: frozenset({S.MANAGING, S.STOPPED, S.CLOSED}),
    # Блокировки обратимы: лимит освободится, данные восстановятся.
    S.RISK_BLOCKED: frozenset({S.WATCH, S.CANCELLED, S.TIMED_OUT}),
    S.DATA_BLOCKED: frozenset({S.WATCH, S.CANCELLED, S.TIMED_OUT}),
    # Терминальные.
    S.STOPPED: frozenset({S.CLOSED}),
    S.TIMED_OUT: frozenset({S.CLOSED}),
    S.CANCELLED: frozenset(),
    S.MISSED: frozenset(),
    S.CLOSED: frozenset(),
}


class TransitionError(RuntimeError):
    """Переход, которого нет в таблице §18."""


def can_transition(old: IdeaStatus, new: IdeaStatus) -> bool:
    return new in ALLOWED.get(old, frozenset())


@dataclass(frozen=True, slots=True)
class TransitionRequest:
    new_status: IdeaStatus
    reason_code: str
    reason_detail: str = ""
    market_snapshot: dict | None = None
    feature_snapshot: dict | None = None
    probability_after: Decimal | None = None
    model_version: str | None = None
    user_action: bool = False


def record_initial(
    session: Session,
    idea: TradeIdea,
    *,
    reason_code: str = "scan",
    reason_detail: str = "",
    market_snapshot: dict | None = None,
) -> IdeaEvent:
    """Первое событие идеи.

    Пишется всегда, даже если идея не будет показана: §12 UX-ТЗ требует
    хранить любую идею, иначе журнал не отвечает на вопрос, что система
    нашла и не показала.
    """
    event = IdeaEvent(
        idea_id=idea.id,
        sequence=1,
        old_status=None,
        new_status=idea.status,
        reason_code=reason_code,
        reason_detail=reason_detail,
        market_snapshot=market_snapshot or {},
        feature_snapshot={},
        probability_after=idea.p_tp1_before_sl,
        config_hash=idea.config_hash,
        engine_version=idea.engine_version,
    )
    session.add(event)
    session.flush()
    return event


def transition(
    session: Session, idea: TradeIdea, request: TransitionRequest
) -> IdeaEvent:
    """Перевести идею в новое состояние и записать событие.

    Проверка идёт до записи: недопустимый переход не должен оставлять следа в
    журнале, иначе история будет содержать состояния, в которых система
    никогда не была.
    """
    old = IdeaStatus(idea.status)
    new = request.new_status
    if old is new:
        raise TransitionError(f"переход {old.value} → {new.value} ничего не меняет")
    if not can_transition(old, new):
        allowed = sorted(s.value for s in ALLOWED.get(old, frozenset()))
        raise TransitionError(
            f"переход {old.value} → {new.value} не разрешён §18; "
            f"из {old.value} можно только в: {allowed or 'никуда, состояние терминальное'}"
        )

    next_sequence = (
        session.execute(
            select(func.coalesce(func.max(IdeaEvent.sequence), 0)).where(
                IdeaEvent.idea_id == idea.id
            )
        ).scalar_one()
        + 1
    )

    event = IdeaEvent(
        idea_id=idea.id,
        sequence=next_sequence,
        old_status=old,
        new_status=new,
        reason_code=request.reason_code,
        reason_detail=request.reason_detail,
        market_snapshot=request.market_snapshot or {},
        feature_snapshot=request.feature_snapshot or {},
        probability_before=idea.p_tp1_before_sl,
        probability_after=request.probability_after,
        config_hash=idea.config_hash,
        engine_version=ENGINE_VERSION,
        model_version=request.model_version,
        user_action=request.user_action,
    )
    session.add(event)
    idea.status = new
    session.flush()
    return event


def history(session: Session, idea_id: UUID) -> list[IdeaEvent]:
    return list(
        session.execute(
            select(IdeaEvent)
            .where(IdeaEvent.idea_id == idea_id)
            .order_by(IdeaEvent.sequence)
        ).scalars()
    )


def terminal_states() -> frozenset[IdeaStatus]:
    return frozenset(s for s, targets in ALLOWED.items() if not targets)
