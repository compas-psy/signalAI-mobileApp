"""Движок HIRING: структура найма (ТЗ §13.3).

Компания, которая собирается что-то построить или запустить, нанимает в
определённом порядке: сначала инженеров и проектировщиков, потом закупки,
потом монтаж и производство, и только потом продажи и сервис. Эта
последовательность видна за кварталы до того, как появится строчка в
отчётности.

Ключевое отличие от наивного счёта вакансий: важно **не сколько**, а
**какие**. Рост числа вакансий сам по себе может означать текучесть, и
§13.3 прямо ограничивает силу такого сигнала. Структурный сдвиг — рост
доли одной функции или выход в новую географию — говорит о намерении;
равномерный рост говорит о том, что люди уходят.

Дедупликация здесь не техническая деталь, а суть измерения. Одна вакансия,
опубликованная двадцать раз, — это одна вакансия. Без склейки любой
активный работодатель выглядит как компания в расширении.

Источники ограничены §13.3: карьерные страницы, разрешённые ATS-выгрузки и
официальная статистика. hh.ru исключён своими же условиями использования, и
проверка этого — на правовом шлюзе, а не здесь.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .base import Component, Evaluation, clamp, q, robust_z, sigmoid

STRATEGY_KEY = "HIRING"
STRATEGY_VERSION = "1.0.0"

# Функции найма (§13.3). Порядок в кортеже — это и есть ожидаемая
# последовательность подготовки к расширению.
SEQUENCE = ("engineering", "procurement", "installation", "operations", "sales")

FUNCTIONS = frozenset(
    SEQUENCE
    + ("service", "regional", "corporate", "compliance", "mass_hiring")
)

WEIGHTS = {
    "structural_shift": 0.35,
    "sequence": 0.25,
    "new_regions": 0.20,
    "salary_premium": 0.10,
    "volume_acceleration": 0.10,
}

# Минимум наблюдений (§13.3).
MIN_WEEKS = 8
MIN_SNAPSHOT_COVERAGE = 0.80

# Потолок силы без структурного сдвига.
NO_SHIFT_CAP = Decimal("0.35")

# Окно поиска последовательности.
SEQUENCE_WINDOW_DAYS = 180


@dataclass(frozen=True, slots=True)
class Vacancy:
    """Вакансия в канонической форме (§13.3)."""

    employer_entity_id: str
    normalized_title: str
    function: str
    region: str
    published_day: int
    salary: Decimal | None = None
    baseline_salary: Decimal | None = None
    reposts: int = 0
    age_days: int = 0

    @property
    def fingerprint(self) -> str:
        """Отпечаток роли: работодатель, должность, регион, функция.

        Двадцать публикаций одной роли — это одна роль. Без этого любой
        активный работодатель выглядит как компания в расширении.
        """
        return "|".join(
            [
                self.employer_entity_id,
                self.normalized_title.strip().lower(),
                self.region.strip().lower(),
                self.function,
            ]
        )


@dataclass
class HiringResult(Evaluation):
    unique_roles: int = 0
    sequence_stages: tuple[str, ...] = ()
    new_regions: tuple[str, ...] = ()
    turnover_proxy: Decimal = Decimal(0)


def unique_roles(vacancies: list[Vacancy]) -> dict[str, Vacancy]:
    """Склеить повторные публикации одной роли."""
    result: dict[str, Vacancy] = {}
    for vacancy in vacancies:
        key = vacancy.fingerprint
        known = result.get(key)
        if known is None or vacancy.published_day > known.published_day:
            result[key] = vacancy
    return result


def sequence_score(roles: list[Vacancy]) -> tuple[Decimal, tuple[str, ...]]:
    """Насколько найм похож на подготовку к расширению.

    Ищется порядок «инженеры → закупки → монтаж → производство → продажи»
    в окне 180 дней. Обратный порядок не считается: сначала нанимать
    продавцов, а потом инженеров — это не подготовка к запуску.
    """
    first_seen: dict[str, int] = {}
    for role in roles:
        if role.function not in SEQUENCE:
            continue
        day = first_seen.get(role.function)
        if day is None or role.published_day < day:
            first_seen[role.function] = role.published_day
    stages = [f for f in SEQUENCE if f in first_seen]
    if len(stages) < 2:
        return Decimal(0), tuple(stages)

    ordered = 0
    pairs = 0
    for earlier, later in zip(stages, stages[1:]):
        pairs += 1
        gap = first_seen[later] - first_seen[earlier]
        if 0 <= gap <= SEQUENCE_WINDOW_DAYS:
            ordered += 1
    if pairs == 0:
        return Decimal(0), tuple(stages)
    # Пропуск одной стадии допускается, но снижает уверенность: доля
    # соблюдённых переходов и есть оценка.
    return q(Decimal(ordered) / Decimal(pairs)), tuple(stages)


def _shift(current: dict[str, int], baseline: dict[str, int]) -> tuple[Decimal, int]:
    """Насколько сместилась структура функций. Второе — сколько функций сместилось.

    Считается по долям, а не по числу: компания, удвоившая найм равномерно,
    структуру не изменила — она просто растёт или теряет людей.
    """
    total_now = sum(current.values()) or 1
    total_was = sum(baseline.values()) or 1
    moved = 0
    biggest = 0.0
    for function in FUNCTIONS:
        share_now = current.get(function, 0) / total_now
        share_was = baseline.get(function, 0) / total_was
        delta = share_now - share_was
        if delta >= 0.10:
            moved += 1
            biggest = max(biggest, delta)
    return q(Decimal(str(clamp(biggest / 0.3)))), moved


def turnover_proxy(roles: list[Vacancy]) -> Decimal:
    """Признак текучести, а не расширения (§13.3).

    Постоянные перепубликации одной роли и высокий возраст вакансии
    означают, что позицию не могут закрыть, — это про уход людей, а не про
    новые проекты.
    """
    if not roles:
        return Decimal(0)
    reposted = sum(1 for r in roles if r.reposts >= 3)
    old = sum(1 for r in roles if r.age_days >= 90)
    return q(Decimal(reposted + old) / Decimal(2 * len(roles)))


def evaluate(
    vacancies: list[Vacancy],
    *,
    baseline_functions: dict[str, int] | None = None,
    known_regions: frozenset[str] = frozenset(),
    weeks_observed: int = 0,
    snapshot_coverage: float = 1.0,
    supplier_confirmation: bool = False,
) -> HiringResult:
    """Оценить структуру найма."""
    result = HiringResult(
        strategy_key=STRATEGY_KEY, strategy_version=STRATEGY_VERSION
    )

    if weeks_observed < MIN_WEEKS:
        result.applicable = False
        result.reason_codes.append("hiring_insufficient_history")
        result.detail = f"наблюдений {weeks_observed} недель из {MIN_WEEKS}"
        return result
    if snapshot_coverage < MIN_SNAPSHOT_COVERAGE:
        result.applicable = False
        result.reason_codes.append("hiring_gaps_in_snapshots")
        result.detail = (
            f"собрано {snapshot_coverage:.0%} ожидаемых снимков: ряд с "
            "дырами показывает ускорение там, где была потеря данных"
        )
        return result

    unresolved = [v for v in vacancies if not v.employer_entity_id]
    if unresolved:
        result.applicable = False
        result.reason_codes.append("hiring_unresolved_employer")
        result.detail = "работодатель не сопоставлен с эмитентом"
        return result

    roles = list(unique_roles(vacancies).values())
    result.unique_roles = len(roles)
    if not roles:
        result.applicable = False
        result.reason_codes.append("hiring_no_roles")
        return result

    current = {f: 0 for f in FUNCTIONS}
    for role in roles:
        if role.function in current:
            current[role.function] += 1

    shift_value, moved = _shift(current, baseline_functions or {})
    seq_value, stages = sequence_score(roles)
    result.sequence_stages = stages

    regions = {r.region for r in roles if r.region}
    fresh_regions = tuple(sorted(regions - known_regions))
    result.new_regions = fresh_regions

    premiums = [
        (r.salary - r.baseline_salary) / r.baseline_salary
        for r in roles
        if r.salary and r.baseline_salary and r.baseline_salary > 0
    ]
    premium = (
        sigmoid(Decimal(str(sum(premiums) / len(premiums))))
        if premiums
        else None
    )

    result.turnover_proxy = turnover_proxy(roles)

    result.components = [
        Component("structural_shift", WEIGHTS["structural_shift"], shift_value,
                  "доля одной функции выросла"),
        Component("sequence", WEIGHTS["sequence"], seq_value,
                  "порядок найма похож на подготовку к запуску"),
        Component(
            "new_regions",
            WEIGHTS["new_regions"],
            q(Decimal(str(clamp(len(fresh_regions) / 3)))),
            "появились новые географии",
        ),
        Component("salary_premium", WEIGHTS["salary_premium"], premium,
                  "платят выше базы региона"),
        Component(
            "volume_acceleration",
            WEIGHTS["volume_acceleration"],
            q(Decimal(str(clamp(len(roles) / 50)))),
            "уникальных ролей стало больше",
        ),
    ]
    result.score()

    # Текучесть вычитается из уверенности: это альтернативное объяснение
    # того же роста, и игнорировать его нельзя.
    if result.turnover_proxy > 0:
        result.confidence = q(
            max(Decimal(0), result.confidence * (Decimal(1) - result.turnover_proxy))
        )
        if result.turnover_proxy >= Decimal("0.4"):
            result.reason_codes.append("hiring_turnover_alternative")

    # Структурный сдвиг обязателен: §13.3 требует минимум двух функций либо
    # функции плюс новой географии. Без него это рост числа вакансий, а он
    # объясняется текучестью не хуже, чем расширением.
    structural = moved >= 2 or (moved >= 1 and fresh_regions)
    if not structural:
        result.reason_codes.append("hiring_no_structural_shift")
        result.strength = min(result.strength, NO_SHIFT_CAP)
        result.direction = "neutral"
        result.detail = (
            "вакансий стало больше, но структура не изменилась: это "
            "объясняется текучестью не хуже, чем расширением"
        )
        return result

    if not supplier_confirmation:
        # §13.3: подтверждение от поставщиков, закупок или капитальных
        # затрат обязательно для подтверждённой гипотезы. Здесь — код
        # причины; решение принимает объединение.
        result.reason_codes.append("hiring_awaiting_supplier_confirmation")

    result.direction = "positive"
    where = f", новых регионов {len(fresh_regions)}" if fresh_regions else ""
    result.detail = (
        f"структура найма сместилась в {moved} функциях{where}; "
        f"стадии подготовки: {', '.join(stages) or 'не выстроены'}"
    )
    return result


__all__ = [
    "FUNCTIONS",
    "HiringResult",
    "MIN_SNAPSHOT_COVERAGE",
    "MIN_WEEKS",
    "NO_SHIFT_CAP",
    "SEQUENCE",
    "STRATEGY_KEY",
    "STRATEGY_VERSION",
    "Vacancy",
    "WEIGHTS",
    "evaluate",
    "sequence_score",
    "turnover_proxy",
    "unique_roles",
]
