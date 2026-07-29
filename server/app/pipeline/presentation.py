"""Доказательства и разметка идеи (§9.1, §10.6 UX-ТЗ).

Детекторы уже выдают метки, но до сих пор они никуда не сохранялись: идея
уходила в приложение с оценкой и планом, а разметка терялась по дороге.
Именно поэтому график в приложении рисует свои уровни вместо того, что нашли
детекторы, — рисовать было нечего.

Здесь два правила, и оба из §9.1.

**Рисуется только то, что участвовало в оценке.** Разметка собирается из тех
же показаний детекторов, из которых собраны компоненты оценки, и каждая метка
ссылается на доказательство по идентификатору. Нарисовать слой, не
участвовавший в решении, физически нечем: неоткуда взять ``evidence_id``.

**Конфликт показывается, а не сглаживается.** Медвежья структура при бычьем
входе, кульминация объёма против направления, событийный риск — всё это
попадает в ``conflicts_with`` доказательства и в счётчик контрфакторов.
Идея, у которой конфликтов больше, чем подтверждений, обязана выглядеть
подозрительно, а не ровно.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from ..detectors.base import Mark, Reading
from ..strategies.base import Candidate

# Логические идентификаторы доказательств. Компоненты оценки §15.1 уже
# ссылаются на них через ``evidence_id``; здесь у ссылок появляется адресат.
EVIDENCE_TITLES: dict[str, str] = {
    "regime": "Режим рынка",
    "smc": "Структура и Smart Money",
    "wyckoff": "Фазы Вайкоффа",
    "setup": "Зона сетапа",
    "price_action": "Триггер Price Action",
    "volume": "Объём и открытый интерес",
    "plan": "План сделки",
}


def _camel(kind: str) -> str:
    """``smc_order_block`` → ``smcOrderBlock``.

    Внутри движка удобнее snake_case, а словарь типов разметки §10.6 задан
    в приложении в camelCase. Перевод делается здесь, на границе контракта,
    а не переименованием детекторов: имя типа — это контракт с интерфейсом,
    и менять его вместе с внутренними именами значит связать несвязанное.
    """
    head, *rest = kind.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _num(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _mark_json(
    mark: Mark, index: int, *, evidence_id: str, detector_version: str
) -> dict[str, Any]:
    return {
        "id": f"{evidence_id}-{index}",
        "type": _camel(mark.kind),
        "timeframe": mark.timeframe,
        "start_time": mark.start_time.isoformat(),
        "end_time": mark.end_time.isoformat(),
        "price_low": _num(mark.price_low),
        "price_high": _num(mark.price_high),
        "confidence": round(float(mark.confidence), 4),
        "evidence_id": evidence_id,
        "detector_version": detector_version,
        "label": mark.label,
        "display_priority": mark.display_priority,
    }


def _level(
    kind: str,
    price: Decimal | None,
    *,
    start: datetime,
    end: datetime,
    timeframe: str,
    label: str,
    priority: int,
) -> dict[str, Any] | None:
    """Уровень плана как метка §10.6.

    Уровни рисуются из того же плана, по которому считался риск, а не из
    отдельного набора чисел: две копии стопа рано или поздно разойдутся, и
    на графике окажется не тот стоп, который стоит в заявке.
    """
    if price is None:
        return None
    return {
        "id": f"plan-{kind}",
        "type": kind,
        "timeframe": timeframe,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "price_low": float(price),
        "price_high": float(price),
        "confidence": 1.0,
        "evidence_id": "plan",
        "detector_version": "plan-1.0.0",
        "label": label,
        "display_priority": priority,
    }


def build_annotations(
    readings: dict[str, Reading | None],
    candidate: Candidate,
    *,
    signal_time: datetime,
    expires_at: datetime,
    trigger_timeframe: str,
) -> list[dict[str, Any]]:
    """Разметка идеи: находки детекторов плюс уровни плана.

    Берутся только показания из ``candidate.used`` — те, на которых стратегия
    действительно построила план. Это и есть §9.1 в исполнимом виде:
    нарисовать слой, не участвовавший в решении, физически нечем.
    """
    out: list[dict[str, Any]] = []
    used = {id(reading) for reading in candidate.used}

    for evidence_id, reading in readings.items():
        if reading is None or id(reading) not in used:
            continue
        for index, mark in enumerate(reading.marks):
            out.append(
                _mark_json(
                    mark, index,
                    evidence_id=evidence_id,
                    detector_version=reading.version,
                )
            )

    # Зона входа — прямоугольник, остальное — линии.
    if candidate.entry_low is not None and candidate.entry_high is not None:
        out.append(
            {
                "id": "plan-entry",
                "type": "levelEntry",
                "timeframe": trigger_timeframe,
                "start_time": signal_time.isoformat(),
                "end_time": expires_at.isoformat(),
                "price_low": float(min(candidate.entry_low, candidate.entry_high)),
                "price_high": float(max(candidate.entry_low, candidate.entry_high)),
                "confidence": 1.0,
                "evidence_id": "plan",
                "detector_version": "plan-1.0.0",
                "label": "Зона входа",
                "display_priority": 95,
            }
        )

    # Инвалидация в плане — условие словами («закрытие ниже границы зоны»),
    # а не цена. Рисовать её линией значит выдумать число, которого движок не
    # считал; она едет в доказательство текстом и показывается в разборе.
    levels: list[tuple[str, Decimal | None, str, int]] = [
        ("levelStop", candidate.stop, "Стоп", 96),
    ]
    for i, target in enumerate(candidate.targets, start=1):
        levels.append(("levelTarget", target.price, f"TP{i}", 80))

    for kind, price, label, priority in levels:
        item = _level(
            kind, price,
            start=signal_time, end=expires_at,
            timeframe=trigger_timeframe, label=label, priority=priority,
        )
        if item is not None:
            # Целей несколько — идентификатор уровня дополняется подписью,
            # иначе три цели схлопнулись бы в одну по совпадающему id.
            item["id"] = f"plan-{kind}-{label}"
            out.append(item)

    out.sort(key=lambda a: (-a["display_priority"], a["start_time"]))
    return out


def build_evidence(
    readings: dict[str, Reading | None],
    candidate: Candidate,
    *,
    regime_summary: str,
) -> list[dict[str, Any]]:
    """Доказательства, на которые ссылаются компоненты оценки и разметка."""
    out: list[dict[str, Any]] = []

    for evidence_id, reading in readings.items():
        if reading is None:
            # Отсутствующее показание — тоже доказательство, только
            # отрицательное. Молча пропустить его значит показать оценку,
            # часть которой не на чем проверить.
            out.append(
                {
                    "id": evidence_id,
                    "kind": evidence_id,
                    "title": EVIDENCE_TITLES.get(evidence_id, evidence_id),
                    "detail": "детектор не дал показаний",
                    "confidence": 0.0,
                    "detector_version": "",
                    "measured": False,
                    "conflicts_with": [],
                }
            )
            continue

        out.append(
            {
                "id": evidence_id,
                "kind": reading.evidence_kind,
                "title": EVIDENCE_TITLES.get(evidence_id, reading.detector),
                "detail": reading.detail or reading.summary,
                "summary": reading.summary,
                "confidence": round(reading.confidence.value, 4),
                "detector_version": reading.version,
                "measured": True,
                "missing_terms": reading.confidence.missing(),
                "measures": [
                    {
                        "name": m.name,
                        "label": m.label,
                        "value": m.value,
                        "status": m.status,
                        "note": m.note,
                    }
                    for m in reading.measures
                ],
                "conflicts_with": [],
            }
        )

    if "regime" not in readings:
        out.append(
            {
                "id": "regime",
                "kind": "market_regime",
                "title": EVIDENCE_TITLES["regime"],
                "detail": regime_summary,
                "confidence": 1.0,
                "detector_version": "regime-1.0.0",
                "measured": True,
                "conflicts_with": [],
            }
        )

    out.append(
        {
            "id": "plan",
            "kind": "trade_plan",
            "title": EVIDENCE_TITLES["plan"],
            "detail": candidate.thesis,
            "confidence": 1.0,
            "detector_version": "plan-1.0.0",
            "measured": True,
            "conflicts_with": [],
        }
    )

    _link_conflicts(out, candidate)
    return out


def _link_conflicts(evidence: list[dict[str, Any]], candidate: Candidate) -> None:
    """Заполнить ``conflictsWith`` по контрфакторам кандидата (§9.1).

    Поле существовало в модели приложения с самого начала и никогда никем не
    заполнялось — то есть панель конфликтов была всегда пуста, чем бы ни
    противоречили друг другу детекторы.
    """
    if not candidate.counter_factors:
        return
    by_id = {item["id"]: item for item in evidence}
    for factor in candidate.counter_factors:
        lowered = factor.lower()
        target = None
        if "структур" in lowered or "bos" in lowered or "choch" in lowered:
            target = by_id.get("smc")
        elif "объём" in lowered or "объем" in lowered or "кульминац" in lowered:
            target = by_id.get("volume") or by_id.get("wyckoff")
        elif "событ" in lowered:
            target = by_id.get("regime")
        elif "триггер" in lowered:
            target = by_id.get("price_action")
        if target is None:
            target = by_id.get("plan")
        if target is not None:
            target.setdefault("conflicts_with", []).append(factor)
