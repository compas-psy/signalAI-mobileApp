"""Движки SPREAD, SUPPLIER, BUDGET, CAPACITY (§13.4–13.7).

Каждый тест — приёмочный критерий ТЗ либо названное там антишумовое
правило. То есть ровно те случаи, где арифметика улучшается, а бизнесу
хуже.
"""

from __future__ import annotations

from decimal import Decimal

from app.research.engines import budget, capacity, spread, supplier
from app.research.engines.budget import Flow
from app.research.engines.capacity import CapacitySnapshot
from app.research.engines.spread import Leg, Period
from app.research.engines.supplier import (
    Contract,
    executed_value,
    independence_groups,
    latest_versions,
)

D = Decimal


# ── SPREAD §13.4 ────────────────────────────────────────────────────────────


def spread_periods(count: int = 8, *, margin_step: D = D(10)) -> list[Period]:
    """Ряд, где удельная маржа растёт равномерно."""
    return [
        Period(
            period=f"2025-{i}",
            products=(Leg("product", D(1000) + margin_step * i, D(1)),),
            inputs=(Leg("feedstock", D(600), D(1)),),
            logistics=D(50),
            variable_tax=D(30),
        )
        for i in range(count)
    ]


def test_веса_spread_складываются_в_единицу():
    assert abs(sum(spread.WEIGHTS.values()) - 1.0) < 1e-9


def test_растущий_спред_даёт_положительное_направление():
    verdict = spread.evaluate(
        spread_periods(), revenue_coverage=0.8, cost_coverage=0.7
    )
    assert verdict.direction == "positive"
    assert verdict.unit_margin > verdict.unit_margin_year_ago


def test_авария_отделяет_благоприятный_спред_от_снимаемого():
    """§13.4: хороший спред при остановке производства не даёт прибыли."""
    rows = spread_periods()
    rows[-1] = Period(
        period=rows[-1].period,
        products=rows[-1].products,
        inputs=rows[-1].inputs,
        logistics=rows[-1].logistics,
        variable_tax=rows[-1].variable_tax,
        outage=True,
    )
    verdict = spread.evaluate(rows, revenue_coverage=0.8, cost_coverage=0.7)
    assert verdict.direction == "neutral"
    assert not verdict.capturable
    assert "spread_outage_not_capturable" in verdict.reason_codes


def test_низкое_покрытие_модели_ограничивает_уверенность():
    """При 50% выручки в прокси гипотеза не становится подтверждённой."""
    verdict = spread.evaluate(
        spread_periods(), revenue_coverage=0.5, cost_coverage=0.4
    )
    assert verdict.confidence <= spread.LOW_COVERAGE_CAP
    assert "spread_low_coverage" in verdict.reason_codes


def test_непроверенная_модель_ограничена_по_уверенности():
    verdict = spread.evaluate(
        spread_periods(), revenue_coverage=0.8, cost_coverage=0.7, calibrated=False
    )
    assert verdict.confidence <= spread.UNCALIBRATED_CAP
    assert "spread_uncalibrated_proxy" in verdict.reason_codes


def test_одиночный_период_это_шум():
    """§13.4 требует двух последовательных окон в одну сторону."""
    rows = spread_periods(margin_step=D(0))
    rows[-1] = Period(
        period="spike",
        products=(Leg("product", D(2000), D(1)),),
        inputs=rows[-1].inputs,
        logistics=rows[-1].logistics,
        variable_tax=rows[-1].variable_tax,
    )
    verdict = spread.evaluate(rows, revenue_coverage=0.8, cost_coverage=0.7)
    assert verdict.direction == "neutral"
    assert "spread_single_period" in verdict.reason_codes


def test_курс_учитывается_в_экспортной_ноге():
    leg = Leg("product_usd", D(100), D(1), fx=D(90))
    assert leg.value == D(9000)


# ── SUPPLIER §13.5 ──────────────────────────────────────────────────────────


def contract(lineage="c-1", version=1, state="award", amount=D(1000), **kw):
    base = dict(
        lineage_id=lineage,
        version=version,
        state=state,
        amount=amount,
        supplier_entity_id=kw.pop("supplier", "s-1"),
        supplier_group_id=kw.pop("group", "g-1"),
    )
    base.update(kw)
    return Contract(**base)


def test_три_версии_контракта_не_утраивают_сумму():
    """Главный приёмочный критерий §13.5."""
    versions = [
        contract(version=1, amount=D(1000)),
        contract(version=2, amount=D(1200)),
        contract(version=3, amount=D(1500)),
    ]
    assert len(latest_versions(versions)) == 1
    # 1500 × вес award 0.45
    assert executed_value(versions) == D("675.00")


def test_связанные_поставщики_это_одна_группа():
    """Три компании одного владельца — один источник, а не три."""
    related = [
        contract(lineage="a", supplier="s-1", group="holding"),
        contract(lineage="b", supplier="s-2", group="holding"),
        contract(lineage="c", supplier="s-3", group="holding"),
    ]
    assert len(independence_groups(related)) == 1


def test_извещение_без_победителя_ограничено_по_силе():
    verdict = supplier.evaluate([contract(state="notice")], [])
    assert verdict.direction == "neutral"
    assert "supplier_notice_only" in verdict.reason_codes


def test_отмена_контракта_создаёт_противоречие():
    verdict = supplier.evaluate(
        [contract(state="award"), contract(lineage="c-2", state="cancelled")],
        [contract(state="award")],
    )
    assert "supplier_contract_cancelled" in verdict.reason_codes


def test_рамочный_договор_не_считается_выборкой():
    """Рамочная максимальная цена — это потолок, а не заказ."""
    plain = executed_value([contract(amount=D(1000))])
    framework = executed_value([contract(amount=D(1000), framework=True)])
    assert framework < plain


def test_закупка_на_замену_не_считается_ростом():
    assert executed_value([contract(replacement=True)]) == D("0.00")


def test_внутригрупповой_контракт_не_считается():
    assert executed_value([contract(intra_group=True)]) == D("0.00")


def test_совпадение_по_названию_блокирует_сигнал():
    """§13.5: связь по ИНН/ОГРН обязательна для подтверждённого сигнала."""
    verdict = supplier.evaluate([contract(strong_id=False)], [])
    assert not verdict.applicable
    assert "supplier_name_only_match" in verdict.reason_codes


def test_широкая_база_поставщиков_даёт_сигнал():
    current = [
        contract(lineage=f"c-{i}", state="advance", group=f"g-{i}")
        for i in range(3)
    ]
    prior = [contract(lineage="old", state="award", amount=D(100))]
    verdict = supplier.evaluate(current, prior)
    assert verdict.direction == "positive"
    assert verdict.breadth == 3


# ── BUDGET §13.6 ────────────────────────────────────────────────────────────


def test_план_без_ассигнований_имеет_нулевой_вес():
    """Приёмочный критерий §13.6."""
    verdict = budget.evaluate([Flow("intent", D(1_000_000))], [])
    assert not verdict.applicable
    assert "budget_intent_only" in verdict.reason_codes


def test_номинальный_рост_ниже_дефлятора_не_импульс():
    current = [Flow("contract", D(108))]
    prior = [Flow("contract", D(100))]
    verdict = budget.evaluate(current, prior, inflation=D("0.10"))
    assert verdict.direction == "negative"
    assert "budget_below_inflation" in verdict.reason_codes


def test_реальный_рост_даёт_положительный_импульс():
    verdict = budget.evaluate(
        [Flow("payment", D(150))],
        [Flow("payment", D(100))],
        inflation=D("0.05"),
        beneficiary_exposure=D("0.3"),
    )
    assert verdict.direction == "positive"
    assert verdict.real_impulse > 0


def test_неизвестная_доля_бенефициара_ограничивает_уверенность():
    """Крупный диверсифицированный эмитент может не заметить программу."""
    verdict = budget.evaluate(
        [Flow("payment", D(150))], [Flow("payment", D(100))], inflation=D("0.05")
    )
    assert "budget_exposure_unknown" in verdict.reason_codes
    assert verdict.confidence <= D("0.50")


def test_субсидия_компенсирующая_убыток_помечается():
    verdict = budget.evaluate(
        [Flow("payment", D(150), subsidy_covers_loss=True)],
        [Flow("payment", D(100))],
        inflation=D("0.05"),
        beneficiary_exposure=D("0.3"),
    )
    assert "budget_subsidy_covers_loss" in verdict.reason_codes


def test_импортный_подрядчик_не_создаёт_выручку_публичного():
    assert budget.weighted_flow([Flow("payment", D(100), foreign_supplier=True)]) == D("0.00")


def test_без_исполнения_подтверждения_нет():
    verdict = budget.evaluate(
        [Flow("limits", D(150))],
        [Flow("limits", D(100))],
        inflation=D("0.05"),
        beneficiary_exposure=D("0.3"),
    )
    assert "budget_not_executed_yet" in verdict.reason_codes


# ── CAPACITY §13.7 ──────────────────────────────────────────────────────────


def capacity_history(**overrides) -> list[CapacitySnapshot]:
    rows = [
        CapacitySnapshot(
            period=f"2025-{i}",
            utilization=D("0.70") + D("0.02") * i,
            inventory_days=D(40) - D(2) * i,
            lead_time_days=D(30) + D(3) * i,
            producer_price=D(100) + D(4) * i,
            output=D(1000) + D(10) * i,
            real_demand_growth=D("0.08"),
        )
        for i in range(6)
    ]
    for key, value in overrides.items():
        index, field = key.split("__")
        row = rows[int(index)]
        values = {n: getattr(row, n) for n in CapacitySnapshot.__dataclass_fields__}
        values[field] = value
        rows[int(index)] = CapacitySnapshot(**values)
    return rows


def test_веса_capacity_складываются_в_единицу():
    assert abs(sum(capacity.WEIGHTS.values()) - 1.0) < 1e-9


def test_дефицит_с_подтверждённым_спросом_даёт_сигнал():
    verdict = capacity.evaluate(capacity_history())
    assert verdict.direction == "positive"


def test_без_подтверждения_спроса_сигнала_нет():
    """Приёмочный критерий §13.7 — и главная защита движка.

    Высокая загрузка и пустой склад так же похожи на остановку линии.
    """
    verdict = capacity.evaluate(capacity_history(**{f"{i}__real_demand_growth": None for i in range(6)}))
    assert not verdict.applicable
    assert "capacity_no_demand_confirmation" in verdict.reason_codes


def test_рост_выпуска_вместе_с_запасами_это_затоваривание():
    """Output up + inventories up без дефицита (§13.7)."""
    rows = capacity_history(**{"5__inventory_days": D(80), "5__output": D(2000)})
    verdict = capacity.evaluate(rows)
    assert verdict.direction == "neutral"
    assert "capacity_inventory_buildup" in verdict.reason_codes


def test_цена_выросшая_от_курса_не_считается_ценовой_силой():
    rows = capacity_history(**{"5__price_driven_by_fx_or_tax": True})
    verdict = capacity.evaluate(rows)
    pricing = next(c for c in verdict.components if c.key == "pricing_power")
    assert not pricing.measured


def test_новые_мощности_в_горизонте_закрывают_дефицит():
    rows = capacity_history(
        **{
            "5__expected_demand_growth": D("0.05"),
            "5__announced_capacity_within_horizon": D("0.10"),
        }
    )
    verdict = capacity.evaluate(rows)
    assert verdict.direction == "neutral"
    assert "capacity_new_supply_arrives" in verdict.reason_codes


def test_слабая_критичность_узла_ограничивает_уверенность():
    """§13.7: связь ниже 0.75 не участвует в подтверждённой гипотезе."""
    verdict = capacity.evaluate(
        capacity_history(), second_order_criticality=D("0.5")
    )
    assert verdict.second_order
    assert "capacity_weak_criticality" in verdict.reason_codes
    assert verdict.confidence <= D("0.50")
