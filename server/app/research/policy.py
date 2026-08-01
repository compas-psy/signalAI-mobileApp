"""Правовой шлюз источников (ТЗ Early Signals §2.1, §5.3).

Шлюз стоит **до** сетевого соединения, а не после. Это не формальность:
разница между «спросили и не получили» и «не спрашивали» — это разница
между нарушением условий использования и его отсутствием. Коллектор без
разрешения обязан падать до открытия сокета.

Правило одно и оно недоговороспособно: **fail-closed**. Разрешает только
явное `approved` с непросроченной проверкой условий и с запрошенными
операциями внутри разрешённых. Всё остальное — включая «мы ещё не
разобрались» — запрещает. Причина в асимметрии последствий: лишний отказ
стоит одного пропущенного обновления, лишнее разрешение — договорной
претензии.

Отдельно про срок проверки условий. Он не украшение: условия использования
меняются, и источник, разрешённый год назад, сегодня может быть разрешён
на других условиях. Просроченная проверка переводит источник в отказ сама,
без чьего-либо участия, — иначе «проверить условия» навсегда останется в
списке дел.

Каждое решение — и разрешение, и отказ — пишется в журнал. Журнал, в
котором есть только успехи, не отвечает на главный вопрос аудита: пытались
ли обойти запрет.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CollectionPermit, ResearchSource
from ..models.enums import LicenseStatus

# Сколько живёт выданное разрешение.
#
# Час, а не сутки: разрешение — снимок правового состояния на момент
# проверки, и долгоживущий пропуск переживёт отзыв условий.
PERMIT_TTL = timedelta(hours=1)

# Операции, которые источник может разрешить.
OPERATIONS = frozenset(
    {"fetch", "store_raw", "transform", "display_derived", "redistribute_raw"}
)


class CollectionDenied(Exception):
    """Сбор запрещён. Несёт причину — отказ без причины нечем исправить."""

    def __init__(self, source_id: str, reason: str):
        self.source_id = source_id
        self.reason = reason
        super().__init__(f"{source_id}: {reason}")


@dataclass(frozen=True, slots=True)
class Permit:
    """Разрешение на сбор из одного источника."""

    source_id: str
    operations: frozenset[str]
    issued_at: datetime
    expires_at: datetime
    permit_id: object | None = None

    def valid_at(self, moment: datetime) -> bool:
        return self.issued_at <= moment < self.expires_at


def _deny(
    session: Session,
    source_id: str,
    operations: set[str],
    reason: str,
    now: datetime,
) -> CollectionDenied:
    """Записать отказ и вернуть исключение для поднятия вызывающим."""
    session.add(
        CollectionPermit(
            source_id=source_id,
            granted=False,
            requested_operations=sorted(operations),
            reason=reason[:1000],
            issued_at=now,
        )
    )
    session.flush()
    return CollectionDenied(source_id, reason)


def authorize(
    session: Session,
    source_id: str,
    operations: set[str] | frozenset[str],
    *,
    now: datetime | None = None,
) -> Permit:
    """Разрешить сбор или отказать с причиной.

    Порядок проверок близок к ТЗ §5.3 и не косметический: сначала
    существование источника, затем право, затем срок проверки условий и
    только потом состав операций. Так причина отказа называет самое раннее
    несоответствие, а не последнее. Единственное отклонение от буквы ТЗ —
    право спрашивается раньше включённости, см. комментарий ниже.
    """
    moment = now or datetime.now(UTC)
    requested = frozenset(operations)

    unknown_ops = requested - OPERATIONS
    if unknown_ops:
        raise _deny(
            session, source_id, set(requested),
            f"неизвестные операции: {', '.join(sorted(unknown_ops))}",
            moment,
        )

    source = session.execute(
        select(ResearchSource).where(ResearchSource.source_id == source_id)
    ).scalar_one_or_none()
    if source is None:
        # Источника нет в реестре — значит его правовой режим никто не
        # проверял. Это тот же неизвестный статус, только хуже.
        raise _deny(
            session, source_id, set(requested),
            "источника нет в реестре: правовой режим не проверялся",
            moment,
        )

    # Право спрашивается раньше включённости, хотя ТЗ §5.3 перечисляет их в
    # обратном порядке. Причина в качестве отказа: запрещённый источник
    # выключен всегда, и ответ «источник выключен» прячет настоящую причину
    # за её следствием. Владельцу нужно знать, что hh.ru не выключили — его
    # нельзя использовать по условиям.
    if source.license_status is not LicenseStatus.APPROVED:
        raise _deny(
            session, source_id, set(requested),
            f"правовой статус «{source.license_status}»: автоматический сбор "
            f"запрещён{'; ' + source.note if source.note else ''}",
            moment,
        )

    if not source.enabled:
        raise _deny(session, source_id, set(requested), "источник выключен", moment)

    due = source.terms_review_due_at
    if due is None:
        raise _deny(
            session, source_id, set(requested),
            "срок проверки условий использования не задан",
            moment,
        )
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    if moment >= due:
        raise _deny(
            session, source_id, set(requested),
            f"проверка условий использования просрочена с {due:%d.%m.%Y}",
            moment,
        )

    allowed = {
        name for name, value in (source.allowed_operations or {}).items() if value
    }
    missing = requested - allowed
    if missing:
        raise _deny(
            session, source_id, set(requested),
            f"источник не разрешает: {', '.join(sorted(missing))}",
            moment,
        )

    row = CollectionPermit(
        source_id=source_id,
        granted=True,
        requested_operations=sorted(requested),
        reason="",
        issued_at=moment,
        expires_at=moment + PERMIT_TTL,
    )
    session.add(row)
    session.flush()
    return Permit(
        source_id=source_id,
        operations=requested,
        issued_at=moment,
        expires_at=moment + PERMIT_TTL,
        permit_id=row.id,
    )


def blocked_sources(session: Session) -> list[tuple[str, str, str]]:
    """Источники, из которых сбор невозможен, и почему.

    Нужны экрану и отчёту о готовности: «сигналов нет» и «источник не
    подключён по праву» — разные новости, и вторая требует действия
    владельца, а не ожидания.
    """
    result: list[tuple[str, str, str]] = []
    for source in session.execute(select(ResearchSource)).scalars():
        if source.license_status is LicenseStatus.APPROVED and source.enabled:
            continue
        why = (
            "выключен"
            if source.license_status is LicenseStatus.APPROVED
            else str(source.license_status)
        )
        result.append((source.source_id, why, source.note))
    result.sort()
    return result


__all__ = [
    "CollectionDenied",
    "OPERATIONS",
    "PERMIT_TTL",
    "Permit",
    "authorize",
    "blocked_sources",
]
