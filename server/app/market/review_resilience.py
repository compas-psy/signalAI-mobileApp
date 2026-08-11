"""Защита crypto-вселенной от кратковременной недоступности Bybit.

``universe.review_universe`` выполняется раз в несколько часов и пересчитывает
``Instrument.is_tradable``. Исторически он трактовал глобальный отказ
``crypto.tickers()`` как корректный пустой снимок. После этого *каждый*
crypto-инструмент получал «инструмента нет в снимке рынка» и становился
``is_tradable=False`` до следующего успешного review.

Это data-plumbing failure, а не рыночный сигнал. Последнее успешно измеренное
состояние нельзя стирать отсутствием измерения. Модуль ставится перед сборкой
scheduler и оборачивает только review; сами пороги допуска не меняются.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DataQualityEvent, Instrument
from ..models.enums import QualityFlag, Venue
from . import universe

# Сохраняется при импорте до install(), поэтому wrapper никогда не рекурсирует.
_ORIGINAL_REVIEW = universe.review_universe
_MISSING_MARKER = "инструмента нет в снимке рынка"


def review_universe_resilient(
    session: Session,
    *,
    now: datetime | None = None,
    fetch=None,
    cfg=None,
) -> universe.AdmissionReport:
    """Review с last-known-good семантикой при глобальной слепоте Bybit.

    Если снимок Bybit действительно доступен, поведение полностью совпадает
    с исходным review: все исторические, оборотные и OI-пороги применяются как
    раньше. Восстановление срабатывает только когда **все** crypto-инструменты
    одновременно получили один и тот же признак отсутствующего snapshot.
    Это отличает отказ источника от обычного выпадения отдельного тикера.
    """
    crypto_instruments = list(
        session.execute(
            select(Instrument).where(
                Instrument.in_universe.is_(True),
                Instrument.venue == Venue.CRYPTO,
            )
        ).scalars()
    )
    before = {
        item.instrument_id: (
            item.is_tradable,
            item.universe_note,
            deepcopy(item.metadata_json or {}),
        )
        for item in crypto_instruments
    }

    report = _ORIGINAL_REVIEW(session, now=now, fetch=fetch, cfg=cfg)
    if not crypto_instruments:
        return report

    verdicts = [
        report.verdicts.get(item.instrument_id) for item in crypto_instruments
    ]
    global_snapshot_loss = bool(verdicts) and all(
        verdict is not None
        and any(_MISSING_MARKER in reason for reason in verdict.reasons)
        for verdict in verdicts
    )
    if not global_snapshot_loss:
        return report

    for item in crypto_instruments:
        was_tradable, note, metadata = before[item.instrument_id]
        item.is_tradable = was_tradable
        item.universe_note = note
        item.metadata_json = metadata
        old_measured = dict((metadata.get("admission") or {}))
        report.verdicts[item.instrument_id] = universe.Verdict(
            admitted=was_tradable,
            reasons=[
                "Bybit snapshot недоступен/пуст — сохранён последний успешный допуск"
            ],
            measured=old_measured,
        )

    session.add(
        DataQualityEvent(
            source="bybit",
            flag=QualityFlag.SOURCE_CONFLICT.value,
            detail=(
                "review получил глобально пустой/недоступный Bybit snapshot; "
                "is_tradable crypto не изменён, сохранён last-known-good admission"
            ),
        )
    )
    report.admitted = sum(1 for verdict in report.verdicts.values() if verdict.admitted)
    session.flush()
    return report


def install() -> None:
    """Подменить review до того, как scheduler захватит ссылку на функцию."""
    if universe.review_universe is not review_universe_resilient:
        universe.review_universe = review_universe_resilient
