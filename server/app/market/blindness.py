"""Почему по инструменту нет баров (§4.4, §24).

Слепота уже называлась поимённо: «без баров 2 (CRYPTO:PERP:HYPEUSDT)».
Этого мало. Имя отвечает на вопрос «по кому мы слепы» и молчит о том,
почему — а действия за «биржа не знает такого символа», «биржа ответила
429» и «инструмент только что появился в отборе, свечи ещё не грузились»
разные, и первые два чинятся, а третий проходит сам.

Причина при этом уже записана: загрузка складывает отказы в
``data_quality_events`` с источником, инструментом, признаком и текстом.
Она просто никогда не доходила до строки журнала, и владелец видел
диагноз без объяснения — ровно ту «отписку», против которой возражал.

Здесь только чтение. Ничего не решается и не чинится: модуль отвечает на
вопрос «что последнее сказал источник об этом инструменте».
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DataQualityEvent

#: Насколько давний отказ ещё объясняет сегодняшнюю слепоту.
#:
#: Неделя, а не вечность: отказ месячной давности, после которого бары
#: приходили нормально, объясняет не сегодняшнее молчание, а прошлое.
LOOKBACK = timedelta(days=7)


def reasons(
    session: Session,
    instrument_ids: list[str],
    *,
    now: datetime | None = None,
    lookback: timedelta = LOOKBACK,
) -> dict[str, str]:
    """Последняя причина отказа по каждому инструменту.

    Инструменты без записанного отказа в ответ не попадают: отсутствие
    причины — тоже сведения, и подставлять вместо неё «источник не ответил»
    значит утверждать то, чего никто не наблюдал.
    """
    if not instrument_ids:
        return {}
    moment = now or datetime.now(UTC)
    rows = session.execute(
        select(
            DataQualityEvent.instrument_id,
            DataQualityEvent.flag,
            DataQualityEvent.detail,
            DataQualityEvent.occurred_at,
        )
        .where(
            DataQualityEvent.instrument_id.in_(instrument_ids),
            DataQualityEvent.occurred_at >= moment - lookback,
        )
        .order_by(DataQualityEvent.occurred_at.desc())
    ).all()

    out: dict[str, str] = {}
    for instrument_id, flag, detail, _ in rows:
        # Первая встреченная запись — самая свежая: сортировка по убыванию.
        if instrument_id in out:
            continue
        text = (detail or "").strip()
        out[instrument_id] = f"{flag}: {text}" if text else flag
    return out


def annotate(
    session: Session,
    instrument_ids: list[str],
    *,
    limit: int = 3,
    now: datetime | None = None,
) -> str:
    """Перечисление слепых инструментов с причинами — строкой для журнала.

    Причина показывается там, где она есть. Инструмент без записанного
    отказа называется просто именем: молчание источника и невыполненная
    загрузка — разные вещи, и склеивать их формулировкой нельзя.
    """
    names = instrument_ids[:limit]
    if not names:
        return ""
    known = reasons(session, names, now=now)
    parts = [f"{name} — {known[name]}" if name in known else name for name in names]
    tail = "" if len(instrument_ids) <= limit else f" и ещё {len(instrument_ids) - limit}"
    return "; ".join(parts) + tail


__all__ = ["LOOKBACK", "annotate", "reasons"]
