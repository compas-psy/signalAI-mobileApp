"""Доказательства и разметка §9.1, §10.6.

Главная проверяемая мысль одна: **на графике не может появиться слой, не
участвовавший в решении**. Это не стилистическое пожелание — рисунок, который
показывает больше, чем знал движок, превращает разбор сделки в самообман.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.detectors.base import Confidence, ConfidenceTerm, Mark, Measure, Reading
from app.models.enums import Direction, OrderIntent, Strategy
from app.pipeline.presentation import build_annotations, build_evidence
from app.strategies.base import Candidate, Check, Target

NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)


def mark(kind: str, *, priority: int = 50) -> Mark:
    return Mark(
        kind=kind,
        timeframe="1h",
        start_time=NOW - timedelta(hours=3),
        end_time=NOW,
        price_low=Decimal("90000"),
        price_high=Decimal("90500"),
        label=kind,
        confidence=0.8,
        display_priority=priority,
    )


def reading(detector: str, *kinds: str) -> Reading:
    return Reading(
        detector=detector,
        version=f"{detector}-1.0.0",
        evidence_kind=f"{detector}_context",
        summary=f"сводка {detector}",
        detail=f"подробности {detector}",
        confidence=Confidence((ConfidenceTerm("основание", 1.0, 0.8),)),
        measures=(Measure("m", "мера", 1.0),),
        marks=tuple(mark(k) for k in kinds),
    )


def candidate(*used: Reading, counter: tuple[str, ...] = ()) -> Candidate:
    return Candidate(
        strategy=Strategy.TREND_PULLBACK,
        direction=Direction.LONG,
        order_intent=OrderIntent.LIMIT_RETEST,
        entry_low=Decimal("90000"),
        entry_high=Decimal("90200"),
        entry_reference=Decimal("90100"),
        stop=Decimal("89500"),
        targets=(
            Target(Decimal("91000"), Decimal("0.5"), "TP1"),
            Target(Decimal("92000"), Decimal("0.3"), "TP2"),
            Target(Decimal("93000"), Decimal("0.2"), "TP3"),
        ),
        checks=(Check("zone", "Зона", True, "вход в зоне"),),
        used=used,
        zones=(),
        invalidation="закрытие ниже границы зоны",
        thesis="тезис",
        counter_factors=counter,
    )


# ─── §9.1: только использованные показания ────────────────────────────────


def test_unused_reading_is_never_drawn():
    """Детектор отработал, но стратегия его не использовала — слоя нет.

    Иначе график показывал бы находку, к решению не относящуюся, и владелец
    объяснял бы сделку тем, чего движок не учитывал.
    """
    used = reading("smc", "smc_bos")
    idle = reading("wyckoff", "wyckoff_range", "wyckoff_spring")

    result = build_annotations(
        {"smc": used, "wyckoff": idle},
        candidate(used),
        signal_time=NOW,
        expires_at=NOW + timedelta(days=5),
        trigger_timeframe="1h",
    )
    types = {a["type"] for a in result}
    assert "smcBos" in types
    assert not {t for t in types if t.startswith("wyckoff")}


def test_every_annotation_points_at_evidence():
    """Метка без доказательства нарисована быть не может (§9.1)."""
    used = reading("smc", "smc_order_block", "smc_fvg")
    result = build_annotations(
        {"smc": used},
        candidate(used),
        signal_time=NOW,
        expires_at=NOW + timedelta(days=5),
        trigger_timeframe="1h",
    )
    assert result
    assert all(a["evidence_id"] for a in result)


def test_kind_is_translated_to_the_interface_vocabulary():
    """Внутри движка snake_case, в разметке §10.6 — словарь интерфейса."""
    used = reading("smc", "smc_order_block", "smc_liquidity_pool")
    result = build_annotations(
        {"smc": used},
        candidate(used),
        signal_time=NOW,
        expires_at=NOW + timedelta(days=5),
        trigger_timeframe="1h",
    )
    types = {a["type"] for a in result}
    assert "smcOrderBlock" in types
    assert "smcLiquidityPool" in types


# ─── Уровни плана ─────────────────────────────────────────────────────────


def test_plan_levels_come_from_the_plan_itself():
    """Стоп на графике — тот же, по которому считался риск.

    Две копии стопа рано или поздно разойдутся, и на графике окажется не тот
    стоп, что стоит в заявке.
    """
    used = reading("smc", "smc_bos")
    result = build_annotations(
        {"smc": used},
        candidate(used),
        signal_time=NOW,
        expires_at=NOW + timedelta(days=5),
        trigger_timeframe="1h",
    )
    by_type: dict[str, list[dict]] = {}
    for a in result:
        by_type.setdefault(a["type"], []).append(a)

    assert by_type["levelStop"][0]["price_low"] == 89500.0
    assert len(by_type["levelTarget"]) == 3
    entry = by_type["levelEntry"][0]
    assert entry["price_low"] == 90000.0 and entry["price_high"] == 90200.0


def test_targets_do_not_collapse_into_one():
    """Три цели — три метки. Совпадающий идентификатор схлопнул бы их."""
    used = reading("smc", "smc_bos")
    result = build_annotations(
        {"smc": used},
        candidate(used),
        signal_time=NOW,
        expires_at=NOW + timedelta(days=5),
        trigger_timeframe="1h",
    )
    ids = [a["id"] for a in result if a["type"] == "levelTarget"]
    assert len(set(ids)) == 3


def test_invalidation_is_not_drawn_as_a_price():
    """Инвалидация — условие словами. Линия по ней была бы выдуманным числом."""
    used = reading("smc", "smc_bos")
    result = build_annotations(
        {"smc": used},
        candidate(used),
        signal_time=NOW,
        expires_at=NOW + timedelta(days=5),
        trigger_timeframe="1h",
    )
    assert not [a for a in result if a["type"] == "levelInvalidation"]


def test_annotations_are_ordered_by_display_priority():
    """§10.7: порядок наложения задан, а не случаен."""
    used = reading("smc", "smc_bos")
    result = build_annotations(
        {"smc": used},
        candidate(used),
        signal_time=NOW,
        expires_at=NOW + timedelta(days=5),
        trigger_timeframe="1h",
    )
    priorities = [a["display_priority"] for a in result]
    assert priorities == sorted(priorities, reverse=True)


# ─── Доказательства ───────────────────────────────────────────────────────


def test_missing_reading_is_recorded_as_negative_evidence():
    """Пропущенное показание — тоже доказательство, только отрицательное.

    Молча его выкинуть значит показать оценку, часть которой не на чем
    проверить.
    """
    result = build_evidence(
        {"smc": reading("smc", "smc_bos"), "price_action": None},
        candidate(),
        regime_summary="тренд вверх",
    )
    by_id = {e["id"]: e for e in result}
    assert by_id["price_action"]["measured"] is False
    assert "не дал показаний" in by_id["price_action"]["detail"]


def test_conflicts_are_attached_to_evidence():
    """§9.1: конфликт показывается, а не сглаживается.

    Поле ``conflicts_with`` существовало в модели приложения с самого начала и
    никогда не заполнялось — панель конфликтов была пуста всегда.
    """
    used = reading("smc", "smc_bos")
    result = build_evidence(
        {"smc": used},
        candidate(used, counter=("структура 4H против направления",)),
        regime_summary="тренд вверх",
    )
    by_id = {e["id"]: e for e in result}
    assert by_id["smc"]["conflicts_with"] == ["структура 4H против направления"]


def test_plan_evidence_always_exists():
    """На доказательство «plan» ссылаются компоненты R/R и уровни графика."""
    result = build_evidence({}, candidate(), regime_summary="флэт")
    assert any(e["id"] == "plan" for e in result)
    assert any(e["id"] == "regime" for e in result)
