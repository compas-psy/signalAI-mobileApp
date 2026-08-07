"""Правовой шлюз источников (ТЗ Early Signals §2.1, §5.3).

Шлюз стоит **до** сетевого соединения, а не после. Это не формальность:
разница между «спросили и не получили» и «не спрашивали» — это разница
между нарушением условий использования и его отсутствием. Коллектор без
разрешения обязан падать до открытия сокета.

Правило одно и оно недоговороспособно: **fail-closed**. Разрешает только
явное `approved` с непросроченной проверкой условий и с запрошенными
операциями внутри разрешённых. Всё остальное запрещает.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CollectionPermit, ResearchSource
from ..models.enums import LicenseStatus

PERMIT_TTL = timedelta(hours=1)
OPERATIONS = frozenset(
    {"fetch", "store_raw", "transform", "display_derived", "redistribute_raw"}
)


class CollectionDenied(Exception):
    def __init__(self, source_id: str, reason: str):
        self.source_id = source_id
        self.reason = reason
        super().__init__(f"{source_id}: {reason}")


@dataclass(frozen=True, slots=True)
class Permit:
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
    """Разрешить сбор или отказать с причиной до открытия сокета."""
    moment = now or datetime.now(UTC)
    requested = frozenset(operations)

    unknown_ops = requested - OPERATIONS
    if unknown_ops:
        raise _deny(
            session,
            source_id,
            set(requested),
            f"неизвестные операции: {', '.join(sorted(unknown_ops))}",
            moment,
        )

    source = session.execute(
        select(ResearchSource).where(ResearchSource.source_id == source_id)
    ).scalar_one_or_none()
    if source is None:
        raise _deny(
            session,
            source_id,
            set(requested),
            "источника нет в реестре: правовой режим не проверялся",
            moment,
        )

    if source.license_status is not LicenseStatus.APPROVED:
        raise _deny(
            session,
            source_id,
            set(requested),
            f"правовой статус «{source.license_status}»: автоматический сбор "
            f"запрещён{'; ' + source.note if source.note else ''}",
            moment,
        )

    due = source.terms_review_due_at
    if due is None:
        raise _deny(
            session,
            source_id,
            set(requested),
            "условия использования не проверялись: срок следующей проверки "
            "не задан",
            moment,
        )
    if due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    if moment >= due:
        raise _deny(
            session,
            source_id,
            set(requested),
            f"проверка условий использования просрочена с {due:%d.%m.%Y}",
            moment,
        )

    if not source.enabled:
        raise _deny(session, source_id, set(requested), "источник выключен", moment)

    allowed = {
        name for name, value in (source.allowed_operations or {}).items() if value
    }
    missing = requested - allowed
    if missing:
        raise _deny(
            session,
            source_id,
            set(requested),
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
