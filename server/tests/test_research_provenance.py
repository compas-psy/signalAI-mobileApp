"""Происхождение и подтверждения: три вещи, которые нельзя смешивать (§2.4).

Смешение уже стоило одного неверного вывода. Коллектор ставил
`lineage_root_id = source_id`, отсюда следовало «два источника — максимум
два корня, правило 3–2–1 недостижимо», и вывод был принят за диагноз.
Неверен он потому, что источник отвечает на вопрос «откуда байты», а
корень — «из чего возник факт».

Здесь проверяется каждый случай, где ошибка тихая: повторная загрузка,
зеркало, ревизия, несколько строк одного периода. Все они выглядят как
новые данные и ни один не является подтверждением.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.research import confirmations as cf
from app.research import provenance as pv

D = Decimal


# ── Корни ───────────────────────────────────────────────────────────────────


def test_повторная_загрузка_не_создаёт_нового_корня():
    """Иначе достаточно запустить сбор трижды, чтобы «подтвердить» что угодно."""
    first = pv.cbr("enterprise_monitoring")
    second = pv.cbr("enterprise_monitoring")
    assert first.lineage_root_id == second.lineage_root_id


def test_разные_наборы_цб_дают_разные_корни():
    """Ключевая ставка и опрос предприятий рождаются по-разному."""
    assert pv.cbr("key_rate").lineage_root_id != pv.cbr(
        "enterprise_monitoring"
    ).lineage_root_id
    assert pv.cbr("key_rate").source_id == pv.cbr("enterprise_monitoring").source_id


def test_строки_и_периоды_одного_набора_корень_не_множат():
    """Июль и август одного опроса — один способ узнать одно и то же.

    Корень не зависит ни от периода, ни от разреза: функция их не
    принимает, и подставить туда строку невозможно по сигнатуре.
    """
    import inspect

    assert list(inspect.signature(pv.cbr).parameters) == ["dataset"]


def test_один_контракт_еис_это_один_корень():
    """Извещение, аванс, приёмка и оплата — этапы жизни одного контракта."""
    stages = [pv.eis_contract("0173100000123000456") for _ in range(4)]
    assert len({s.lineage_root_id for s in stages}) == 1


def test_разные_контракты_разных_групп_дают_независимость():
    """Независимость даёт не другой контракт, а другая экономическая группа.

    Два контракта одного холдинга — одно решение, рассказанное дважды.
    """
    один_холдинг = [
        pv.eis_contract("A", supplier_group="holding-1"),
        pv.eis_contract("B", supplier_group="holding-1"),
    ]
    разные = [
        pv.eis_contract("A", supplier_group="holding-1"),
        pv.eis_contract("B", supplier_group="holding-2"),
    ]
    assert pv.independent_roots(один_холдинг) == 1
    assert pv.independent_roots(разные) == 2


def test_четыре_набора_фнс_это_один_свидетель():
    """Четыре корня и один способ: всё собрано налоговым администрированием.

    Считать их четырьмя подтверждениями значит подтверждать показание
    свидетеля его же словами.
    """
    наборы = [pv.fns(n) for n in ("revenue_expenses", "headcount", "paid_taxes", "tax_debt")]
    assert pv.distinct_roots(наборы) == 4
    assert pv.independent_roots(наборы) == 1


def test_два_источника_дают_больше_одного_независимого_корня():
    """Прямое опровержение прежнего диагноза.

    Опрос предприятий и налоговое администрирование — разные способы
    формирования данных, и подключено их два, а не «максимум два корня».
    """
    смесь = [pv.cbr("enterprise_monitoring"), pv.fns("revenue_expenses")]
    assert pv.independent_roots(смесь) == 2


def test_корень_не_случайный():
    """Случайный идентификатор на строку ломает ровно то правило, ради
    которого всё это существует."""
    assert pv.cbr("key_rate").lineage_root_id == "cbr:key_rate"
    assert "uuid" not in pv.fns("headcount").lineage_root_id.lower()


# ── Подтверждения ───────────────────────────────────────────────────────────


def чтение(period: date, strength="0.6", revision=0, direction="positive"):
    return cf.Reading(
        fingerprint="demand:C",
        period_end=period,
        direction=direction,
        strength=D(strength),
        revision=revision,
    )


def test_подтверждение_это_другой_период():
    result = cf.count([чтение(date(2026, 5, 31)), чтение(date(2026, 6, 30))])
    assert result.periods == 2


def test_повторный_запуск_подтверждением_не_является():
    """Иначе система подтверждала бы гипотезы собственной активностью."""
    одно = чтение(date(2026, 6, 30))
    result = cf.count([одно, одно, одно])
    assert result.periods == 1
    assert result.rejected.get("повтор того же периода") == 2


def test_ревизия_того_же_значения_не_подтверждает():
    """Тот же факт, рассказанный точнее, — не второй факт."""
    result = cf.count([
        чтение(date(2026, 6, 30), revision=0),
        чтение(date(2026, 6, 30), revision=1),
    ])
    assert result.periods == 1
    assert result.rejected.get("ревизия того же периода") == 1


def test_несколько_строк_одного_периода_считаются_один_раз():
    result = cf.count([
        чтение(date(2026, 6, 30), strength="0.6"),
        чтение(date(2026, 6, 30), strength="0.7"),
    ])
    assert result.periods == 1


def test_смена_направления_не_подтверждает():
    result = cf.count([
        чтение(date(2026, 5, 31), direction="negative"),
        чтение(date(2026, 6, 30), direction="positive"),
    ])
    assert result.periods == 1
    assert result.rejected.get("направление не совпадает") == 1


def test_слабый_сигнал_подтверждением_не_считается():
    """Колебание около нуля повторяется само и подтверждает только шум."""
    result = cf.count([
        чтение(date(2026, 5, 31), strength="0.01"),
        чтение(date(2026, 6, 30), strength="0.6"),
    ])
    assert result.periods == 1
    assert result.rejected.get("сила ниже порога") == 1


def test_стадии_события_разносятся_во_времени():
    """Извещение и заключение в один день — одно решение, а не два."""
    day = date(2026, 6, 1)
    близко = cf.count_event_stages([(day, "award"), (day + timedelta(days=2), "advance")])
    далеко = cf.count_event_stages([(day, "award"), (day + timedelta(days=30), "advance")])
    assert близко.periods == 1
    assert далеко.periods == 2


# ── История по периодичности ────────────────────────────────────────────────


def test_требуемая_история_считается_из_периодичности():
    """Общий порог был одинаково неверен для всех рядов."""
    assert cf.required_periods("monthly") == 14
    assert cf.required_periods("quarterly") == 6
    assert cf.required_periods("yearly") == 3


def test_месячный_ряд_не_проходит_квартальным_порогом():
    ok, why = cf.history_verdict(6, "monthly")
    assert not ok
    assert "6 из 14" in why
    assert cf.history_verdict(6, "quarterly")[0]


def test_годовому_ряду_не_нужно_четырнадцати_лет():
    assert cf.history_verdict(3, "yearly")[0]
    assert not cf.history_verdict(2, "yearly")[0]


def test_пороги_три_два_один_не_ослаблены():
    """Ничего из сделанного не должно снижать само правило."""
    from app.research.scoring import (
        MIN_CONFIRMATIONS,
        MIN_DATA_TYPES,
        MIN_LINEAGE_ROOTS,
    )

    assert (MIN_LINEAGE_ROOTS, MIN_DATA_TYPES, MIN_CONFIRMATIONS) == (3, 2, 2)
