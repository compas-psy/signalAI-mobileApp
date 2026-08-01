"""Российские движки: DEMAND и HIRING (§13.2, §13.3).

Проверяются приёмочные критерии ТЗ — то есть ровно те случаи, где наивная
реализация даёт обратный ответ.
"""

from __future__ import annotations

from decimal import Decimal

from app.research.engines import demand, hiring
from app.research.engines.demand import DemandPeriod
from app.research.engines.hiring import Vacancy, sequence_score, unique_roles

D = Decimal


# ── DEMAND ──────────────────────────────────────────────────────────────────


def demand_series(**overrides) -> list[DemandPeriod]:
    rows = [
        DemandPeriod(
            period=f"2025-Q{i % 4 + 1}",
            search_volume=D(100) + D(2) * i,
            nominal_spend=D(1000) + D(30) * i,
            physical_volume=D(500) + D(10) * i,
            category_inflation=D("0.05"),
        )
        for i in range(8)
    ]
    for key, value in overrides.items():
        index, field = key.split("__")
        rows[int(index)] = replace_period(rows[int(index)], **{field: value})
    return rows


def replace_period(row: DemandPeriod, **changes) -> DemandPeriod:
    values = {name: getattr(row, name) for name in DemandPeriod.__dataclass_fields__}
    values.update(changes)
    return DemandPeriod(**values)


def test_веса_demand_складываются_в_единицу():
    assert abs(sum(demand.WEIGHTS.values()) - 1.0) < 1e-9


def test_рост_запросов_без_реальных_трат_не_подтверждается():
    """Приёмочный критерий ТЗ: реклама поднимает запросы, спрос — нет."""
    rows = demand_series()
    for i in range(len(rows)):
        rows[i] = replace_period(
            rows[i],
            search_volume=D(100) + D(30) * i,
            nominal_spend=None,
            physical_volume=None,
            ad_campaign=True,
        )
    verdict = demand.evaluate(rows)
    assert verdict.direction == "neutral"
    assert "demand_promotion_only" in verdict.reason_codes


def test_номинальный_рост_ниже_инфляции_это_падение():
    """20% роста при 25% инфляции — отрицательный реальный рост (§13.2)."""
    rows = demand_series()
    for i in range(len(rows)):
        rows[i] = replace_period(rows[i], physical_volume=None)
    rows[-1] = replace_period(
        rows[-1],
        nominal_spend=rows[-5].nominal_spend * D("1.20"),
        category_inflation=D("0.25"),
    )
    verdict = demand.evaluate(rows)
    assert verdict.direction == "negative"
    assert "demand_below_inflation" in verdict.reason_codes


def test_без_физического_объёма_оценка_ограничена_сверху():
    """Рост запросов и трат без штук — гипотеза о внимании, а не о спросе."""
    rows = [replace_period(r, physical_volume=None) for r in demand_series()]
    verdict = demand.evaluate(rows)
    assert verdict.economic_cap == demand.NO_VOLUME_CAP
    assert verdict.confidence <= demand.NO_VOLUME_CAP
    assert "demand_no_physical_volume" in verdict.reason_codes


def test_дефицит_товара_не_считается_ростом_продаж():
    """Спрос выше предложения: продать было нечего."""
    rows = demand_series()
    rows[-1] = replace_period(
        rows[-1],
        search_volume=D(300),
        physical_volume=D(100),
        out_of_stock=True,
    )
    verdict = demand.evaluate(rows)
    assert verdict.direction == "neutral"
    assert "demand_unmet_out_of_stock" in verdict.reason_codes


def test_рост_объёма_при_падении_маржи_помечается_скидками():
    rows = demand_series()
    rows[-1] = replace_period(
        rows[-1], physical_volume=D(900), margin_change=D("-0.05")
    )
    verdict = demand.evaluate(rows)
    assert "demand_promotion_driven" in verdict.reason_codes


def test_согласованные_компоненты_дают_положительное_направление():
    verdict = demand.evaluate(demand_series())
    assert verdict.direction == "positive"
    assert verdict.aligned >= demand.MIN_ALIGNED


def test_короткая_история_не_даёт_сигнала():
    verdict = demand.evaluate(demand_series()[:3])
    assert not verdict.applicable


# ── HIRING ──────────────────────────────────────────────────────────────────


def vacancy(function: str, day: int, *, region="Москва", title=None, **kw) -> Vacancy:
    return Vacancy(
        employer_entity_id="issuer-1",
        normalized_title=title or f"{function} специалист",
        function=function,
        region=region,
        published_day=day,
        **kw,
    )


def test_веса_hiring_складываются_в_единицу():
    assert abs(sum(hiring.WEIGHTS.values()) - 1.0) < 1e-9


def test_двадцать_дублей_одной_вакансии_это_одна_роль():
    """Без склейки любой активный работодатель выглядит как расширение."""
    duplicates = [vacancy("engineering", day) for day in range(20)]
    assert len(unique_roles(duplicates)) == 1


def test_прямой_порядок_подготовки_повышает_оценку_а_обратный_нет():
    """Сначала продавцы, потом инженеры — это не подготовка к запуску."""
    forward = [
        vacancy("engineering", 0),
        vacancy("procurement", 30),
        vacancy("installation", 60),
        vacancy("operations", 90),
    ]
    backward = [
        vacancy("operations", 0),
        vacancy("installation", 30),
        vacancy("procurement", 60),
        vacancy("engineering", 90),
    ]
    ahead, _ = sequence_score(forward)
    behind, _ = sequence_score(backward)
    assert ahead > behind
    assert ahead == D(1)


def test_рост_без_структурного_сдвига_ограничен_по_силе():
    """Равномерный рост объясняется текучестью не хуже, чем расширением."""
    flat = [
        vacancy(function, day)
        for day in (0, 20, 40)
        for function in ("engineering", "operations", "sales", "corporate")
    ]
    verdict = hiring.evaluate(
        flat,
        baseline_functions={"engineering": 3, "operations": 3, "sales": 3,
                            "corporate": 3},
        weeks_observed=12,
    )
    assert verdict.direction == "neutral"
    assert verdict.strength <= hiring.NO_SHIFT_CAP
    assert "hiring_no_structural_shift" in verdict.reason_codes


def test_структурный_сдвиг_и_новая_география_дают_сигнал():
    roles = [
        vacancy("engineering", 0, title="инженер 1"),
        vacancy("engineering", 5, title="инженер 2"),
        vacancy("engineering", 10, title="инженер 3"),
        vacancy("procurement", 40, title="закупщик", region="Казань"),
        vacancy("installation", 80, title="монтажник", region="Казань"),
    ]
    verdict = hiring.evaluate(
        roles,
        baseline_functions={"corporate": 10},
        known_regions=frozenset({"Москва"}),
        weeks_observed=16,
        supplier_confirmation=True,
    )
    assert verdict.direction == "positive"
    assert "Казань" in verdict.new_regions


def test_текучесть_снижает_уверенность():
    """Перепубликуемая годами вакансия — про уход людей, а не про проекты."""
    stable = [
        vacancy("engineering", 0, title="и1"),
        vacancy("engineering", 5, title="и2"),
        vacancy("engineering", 9, title="и3"),
        vacancy("procurement", 40, title="з1", region="Казань"),
    ]
    churn = [
        vacancy("engineering", 0, title="и1", reposts=5, age_days=200),
        vacancy("engineering", 5, title="и2", reposts=6, age_days=180),
        vacancy("engineering", 9, title="и3", reposts=4, age_days=150),
        vacancy("procurement", 40, title="з1", region="Казань", reposts=5,
                age_days=190),
    ]
    calm = hiring.evaluate(stable, baseline_functions={"corporate": 10},
                           known_regions=frozenset({"Москва"}), weeks_observed=16)
    noisy = hiring.evaluate(churn, baseline_functions={"corporate": 10},
                            known_regions=frozenset({"Москва"}), weeks_observed=16)
    assert noisy.confidence < calm.confidence
    assert "hiring_turnover_alternative" in noisy.reason_codes


def test_дыры_в_наблюдениях_блокируют_сигнал():
    """Ряд с дырами показывает ускорение там, где была потеря данных."""
    verdict = hiring.evaluate(
        [vacancy("engineering", 0)], weeks_observed=16, snapshot_coverage=0.5
    )
    assert not verdict.applicable
    assert "hiring_gaps_in_snapshots" in verdict.reason_codes


def test_несопоставленный_работодатель_блокирует_сигнал():
    orphan = Vacancy(
        employer_entity_id="",
        normalized_title="инженер",
        function="engineering",
        region="Москва",
        published_day=0,
    )
    verdict = hiring.evaluate([orphan], weeks_observed=16)
    assert not verdict.applicable
    assert "hiring_unresolved_employer" in verdict.reason_codes
