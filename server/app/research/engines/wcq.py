"""Движок WCQ: качество роста через оборотный капитал (ТЗ §13.1).

Вопрос, на который движок отвечает: превращается ли рост выручки в деньги —
или оседает в дебиторской задолженности и на складе. Разница между этими
двумя случаями в отчёте о прибылях не видна вовсе: выручка и прибыль растут
одинаково, а денежный поток расходится, и расходится он за несколько
кварталов до того, как проблема доберётся до заголовков.

Почему это ранний сигнал. Отгрузили и записали выручку — событие
сегодняшнее. Получили деньги — событие через квартал. Не получили — узнают
ещё позже. Оборотный капитал показывает разрыв раньше, чем он станет
убытком.

Три ловушки, из-за которых наивная реализация даёт обратный ответ, и все три
здесь закрыты явно:

* **Высвобождение оборотного капитала бывает от беды.** Запасы падают,
  когда распродают склад при падающем спросе, — и метрика улучшается ровно
  в тот момент, когда бизнесу хуже. Поэтому падение запасов вместе с
  падением выручки и маржи не даёт положительного сигнала.
* **Рост DPO — не достижение.** Дольше платить поставщикам можно от силы
  переговорной позиции, а можно от нехватки денег. Само по себе удлинение
  оплаты позитивом не считается (§13.1, антишумовая проверка 7).
* **Квартал нельзя умножать на четыре.** Сезонность у оборотного капитала
  сильнее, чем у выручки: конец года выглядит как улучшение у половины
  отрасли. Потоки считаются за скользящие двенадцать месяцев, запасы — как
  среднее начала и конца периода.

Банки и страховщики исключены. У них оборотный капитал означает другое, и
DSO страховой компании — это не срок инкассации, а артефакт.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from math import exp
from statistics import median

STRATEGY_KEY = "WCQ"
STRATEGY_VERSION = "1.0.0"

# Дней в году для оборачиваемости. 365, а не 360: ТЗ §13.1 фиксирует именно
# эту константу, и «банковский год» дал бы систематическое расхождение с
# отчётностью эмитента.
DAYS = Decimal(365)

EPS = Decimal("1e-12")

# Веса компонентов §13.1. Сумма единица — проверяется тестом.
WEIGHTS = {
    "ar_growth_gap": 0.25,
    "inventory_growth_gap": 0.20,
    "ccc_change": 0.20,
    "cash_conversion_change": 0.20,
    "advance_ratio_change": 0.10,
    "gross_margin": 0.05,
}

# Минимум кварталов: шесть на раннего кандидата, восемь на подтверждение.
MIN_QUARTERS_EARLY = 6
MIN_QUARTERS_CONFIRMED = 8

# Отрасли, к которым движок неприменим.
EXCLUDED_SECTORS = frozenset({"banks", "insurance", "financials"})

# Насколько снижается качество данных, если начальный баланс недоступен.
MISSING_OPENING_BALANCE_PENALTY = Decimal("0.15")


@dataclass(frozen=True, slots=True)
class Quarter:
    """Один отчётный период эмитента.

    Все величины — за квартал (потоки) либо на конец квартала (запасы).
    ``None`` означает «не раскрыто», и это не ноль: отсутствие строки в
    отчёте и нулевое значение строки — разные факты (§11.6).
    """

    period_end: str
    revenue: Decimal | None = None
    cogs: Decimal | None = None
    gross_profit: Decimal | None = None
    ebitda: Decimal | None = None
    cfo: Decimal | None = None
    accounts_receivable: Decimal | None = None
    inventory: Decimal | None = None
    accounts_payable: Decimal | None = None
    customer_advances: Decimal | None = None
    # Флаги, меняющие смысл чисел (§13.1, антишумовые проверки).
    factoring: bool = False
    perimeter_changed: bool = False
    one_off_advance: bool = False


@dataclass(frozen=True, slots=True)
class Component:
    key: str
    weight: float
    value: Decimal | None
    note: str

    @property
    def measured(self) -> bool:
        return self.value is not None


@dataclass
class Evaluation:
    """Итог движка: направление, сила и почему именно так."""

    applicable: bool = True
    direction: str = "neutral"
    strength: Decimal = Decimal(0)
    confidence: Decimal = Decimal(0)
    data_quality: Decimal = Decimal(1)
    components: list[Component] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    detail: str = ""
    metrics: dict[str, Decimal] = field(default_factory=dict)
    quarters_used: int = 0

    @property
    def confirmed_ready(self) -> bool:
        return self.quarters_used >= MIN_QUARTERS_CONFIRMED


def _q(value: Decimal, places: str = "0.0001") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _ttm(values: list[Decimal | None]) -> Decimal | None:
    """Скользящие двенадцать месяцев: сумма последних четырёх кварталов.

    Один пропущенный квартал делает сумму несопоставимой с предыдущей — и
    подставлять на его место среднее нельзя: получится сглаженный ряд,
    который выглядит устойчивее реальности.
    """
    if len(values) < 4:
        return None
    tail = values[-4:]
    if any(v is None for v in tail):
        return None
    return sum(tail)  # type: ignore[arg-type]


def _average_balance(
    values: list[Decimal | None], index: int
) -> tuple[Decimal | None, bool]:
    """Средний запас за период. Второй элемент — пришлось ли брать конец.

    Средний, а не конечный: запас на одну дату сравнивается с потоком за год,
    и конец декабря у сезонного бизнеса даст оборачиваемость, которой не было
    ни одного дня.
    """
    current = values[index]
    if current is None:
        return None, False
    if index == 0 or values[index - 1] is None:
        return current, True
    return (current + values[index - 1]) / 2, False


def _growth(current: Decimal | None, past: Decimal | None) -> Decimal | None:
    """Рост в долях. Для величин, которые могут менять знак, — через медиану.

    Логарифм здесь неприменим: дебиторская задолженность бывает нулевой, а
    денежный поток отрицательным, и ``ln`` на них не определён.
    """
    if current is None or past is None:
        return None
    if past > 0 and current > 0:
        from math import log

        return Decimal(str(log(float(current) / float(past))))
    base = max(abs(past), EPS)
    return (current - past) / base


def _mad_z(series: list[Decimal], value: Decimal) -> Decimal | None:
    """Устойчивый z-счёт (§11.3).

    Через медиану и MAD, а не через среднее и σ: одна разовая сделка
    сдвигает среднее так, что нормальный квартал выглядит выбросом. При
    нулевом MAD переходим на межквартильный размах, а если и он ноль —
    z-счёт не считается вовсе. Ноль вместо него означал бы «изменений нет»,
    что неправда: правда в том, что мерить нечем.
    """
    if len(series) < 4:
        return None
    med = Decimal(str(median(float(x) for x in series)))
    deviations = sorted(abs(x - med) for x in series)
    mad = Decimal(str(median(float(x) for x in deviations)))
    scale = Decimal("1.4826") * mad
    if scale <= 0:
        ordered = sorted(series)
        n = len(ordered)
        iqr = ordered[int(n * 0.75) - 1] - ordered[int(n * 0.25)]
        scale = iqr / Decimal(2)
    if scale <= 0:
        return None
    z = (value - med) / (scale + EPS)
    return max(Decimal(-5), min(Decimal(5), z))


def _sigmoid(z: Decimal) -> Decimal:
    """Перевод z-счёта в [0,1] через сигмоиду с масштабом 1.5 (§13.1)."""
    return _q(Decimal(str(1.0 / (1.0 + exp(-float(z) / 1.5)))))


@dataclass(frozen=True, slots=True)
class Ratios:
    """Оборачиваемость и конверсия за один период."""

    dso: Decimal | None
    dio: Decimal | None
    dpo: Decimal | None
    ccc: Decimal | None
    cash_conversion: Decimal | None
    advance_ratio: Decimal | None
    gross_margin: Decimal | None
    used_ending_balance: bool


def ratios_at(quarters: list[Quarter], index: int) -> Ratios:
    """Показатели §13.1 на конец периода ``index``.

    Формулы дословно из ТЗ:

    ``DSO = average_AR / TTM_revenue × 365``
    ``DIO = average_inventory / |TTM_COGS| × 365``
    ``DPO = average_AP / |TTM_COGS| × 365``
    ``CCC = DSO + DIO − DPO``
    """
    window = quarters[: index + 1]
    revenue = _ttm([q.revenue for q in window])
    cogs = _ttm([q.cogs for q in window])
    cfo = _ttm([q.cfo for q in window])
    ebitda = _ttm([q.ebitda for q in window])

    ar, ar_ending = _average_balance([q.accounts_receivable for q in quarters], index)
    inv, inv_ending = _average_balance([q.inventory for q in quarters], index)
    ap, ap_ending = _average_balance([q.accounts_payable for q in quarters], index)

    dso = ar / revenue * DAYS if ar is not None and revenue and revenue > 0 else None
    cogs_abs = abs(cogs) if cogs is not None else None
    dio = inv / cogs_abs * DAYS if inv is not None and cogs_abs else None
    dpo = ap / cogs_abs * DAYS if ap is not None and cogs_abs else None
    ccc = (
        dso + dio - dpo
        if dso is not None and dio is not None and dpo is not None
        else None
    )
    conversion = (
        cfo / max(abs(ebitda), EPS)
        if cfo is not None and ebitda is not None
        else None
    )
    advances = quarters[index].customer_advances
    advance_ratio = (
        advances / revenue if advances is not None and revenue and revenue > 0 else None
    )
    gross = _ttm([q.gross_profit for q in window])
    margin = gross / revenue if gross is not None and revenue and revenue > 0 else None

    return Ratios(
        dso=dso,
        dio=dio,
        dpo=dpo,
        ccc=ccc,
        cash_conversion=conversion,
        advance_ratio=advance_ratio,
        gross_margin=margin,
        used_ending_balance=ar_ending or inv_ending or ap_ending,
    )


def _direction_rules(
    quarters: list[Quarter],
    now: Ratios,
    year_ago: Ratios,
    revenue_growth: Decimal | None,
) -> tuple[str, list[str]]:
    """Правила направления и антишумовые проверки §13.1.

    Отдельной функцией, потому что именно здесь наивная реализация
    ошибается: арифметика улучшилась — значит хорошо. Не значит.
    """
    codes: list[str] = []

    latest = quarters[-1]
    if latest.factoring:
        codes.append("wcq_factoring")
    if latest.perimeter_changed:
        codes.append("wcq_perimeter_changed")
    if latest.one_off_advance:
        codes.append("wcq_one_off_advance")

    margin_falling = (
        now.gross_margin is not None
        and year_ago.gross_margin is not None
        and now.gross_margin < year_ago.gross_margin
    )
    revenue_falling = revenue_growth is not None and revenue_growth < 0

    # Высвобождение оборотного капитала при падающих продажах и марже —
    # распродажа склада, а не улучшение управления. §13.1: нейтрально.
    if (
        now.ccc is not None
        and year_ago.ccc is not None
        and now.ccc < year_ago.ccc
        and revenue_falling
        and margin_falling
    ):
        codes.append("wcq_release_on_declining_sales")
        return "neutral", codes

    conversion_worse = (
        now.cash_conversion is not None
        and year_ago.cash_conversion is not None
        and now.cash_conversion < year_ago.cash_conversion
    )
    if conversion_worse and revenue_growth is not None and revenue_growth > 0:
        codes.append("wcq_profit_without_cash")
        return "negative", codes

    if (
        now.ccc is not None
        and year_ago.ccc is not None
        and now.ccc < year_ago.ccc
        and not conversion_worse
        and not revenue_falling
    ):
        codes.append("wcq_cash_backed_growth")
        return "positive", codes

    return "neutral", codes


def evaluate(
    quarters: list[Quarter],
    *,
    sector: str = "",
) -> Evaluation:
    """Оценить качество роста по ряду кварталов.

    ``quarters`` — в хронологическом порядке, последний элемент самый свежий.
    Возвращает наблюдение, а не рекомендацию: направление говорит о движении
    показателя, а не о стороне сделки.
    """
    result = Evaluation()

    if sector.lower() in EXCLUDED_SECTORS:
        result.applicable = False
        result.reason_codes.append("wcq_sector_not_applicable")
        result.detail = (
            f"отрасль «{sector}»: оборотный капитал банка или страховщика "
            "означает другое, и оборачиваемость к нему неприменима"
        )
        return result

    # Восемь кварталов нужны не для красоты: пять уходят на два ряда TTM
    # (текущий и годом ранее), остальные — на медиану для z-счёта.
    if len(quarters) < MIN_QUARTERS_EARLY:
        result.applicable = False
        result.reason_codes.append("wcq_insufficient_history")
        result.detail = (
            f"кварталов {len(quarters)} из {MIN_QUARTERS_EARLY}: "
            "скользящий год не с чем сравнить"
        )
        return result

    result.quarters_used = len(quarters)
    last = len(quarters) - 1
    now = ratios_at(quarters, last)
    if last < 4:
        result.applicable = False
        result.reason_codes.append("wcq_insufficient_history")
        result.detail = "нет сопоставимого периода годом ранее"
        return result
    year_ago = ratios_at(quarters, last - 4)

    if now.used_ending_balance:
        result.data_quality -= MISSING_OPENING_BALANCE_PENALTY
        result.reason_codes.append("wcq_ending_balance_used")

    revenue_now = _ttm([q.revenue for q in quarters[: last + 1]])
    revenue_past = _ttm([q.revenue for q in quarters[: last - 3]])
    cogs_now = _ttm([q.cogs for q in quarters[: last + 1]])
    cogs_past = _ttm([q.cogs for q in quarters[: last - 3]])
    ar_now = quarters[last].accounts_receivable
    ar_past = quarters[last - 4].accounts_receivable
    inv_now = quarters[last].inventory
    inv_past = quarters[last - 4].inventory

    revenue_growth = _growth(revenue_now, revenue_past)
    ar_growth = _growth(ar_now, ar_past)
    cogs_growth = _growth(cogs_now, cogs_past)
    inv_growth = _growth(inv_now, inv_past)

    ar_gap = (
        revenue_growth - ar_growth
        if revenue_growth is not None and ar_growth is not None
        else None
    )
    inv_gap = (
        cogs_growth - inv_growth
        if cogs_growth is not None and inv_growth is not None
        else None
    )
    ccc_delta = (
        year_ago.ccc - now.ccc
        if now.ccc is not None and year_ago.ccc is not None
        else None
    )
    conversion_delta = (
        now.cash_conversion - year_ago.cash_conversion
        if now.cash_conversion is not None and year_ago.cash_conversion is not None
        else None
    )
    advance_delta = (
        now.advance_ratio - year_ago.advance_ratio
        if now.advance_ratio is not None and year_ago.advance_ratio is not None
        else None
    )
    margin_delta = (
        now.gross_margin - year_ago.gross_margin
        if now.gross_margin is not None and year_ago.gross_margin is not None
        else None
    )

    # Ряды для z-счёта: те же величины по всем доступным периодам.
    ccc_series = [
        r.ccc for r in (ratios_at(quarters, i) for i in range(4, last + 1))
        if r.ccc is not None
    ]
    conversion_series = [
        r.cash_conversion
        for r in (ratios_at(quarters, i) for i in range(4, last + 1))
        if r.cash_conversion is not None
    ]

    def component(key: str, raw: Decimal | None, series: list[Decimal], note: str) -> Component:
        if raw is None:
            return Component(key, WEIGHTS[key], None, note)
        z = _mad_z(series, raw) if series else None
        # Ряда нет — берём саму величину как z-подобную: она уже выражена в
        # долях и центрирована около нуля по построению (разность ростов).
        value = _sigmoid(z if z is not None else raw)
        return Component(key, WEIGHTS[key], value, note)

    ar_series = []
    inv_series = []
    result.components = [
        component("ar_growth_gap", ar_gap, ar_series,
                  "выручка растёт быстрее дебиторской задолженности"),
        component("inventory_growth_gap", inv_gap, inv_series,
                  "себестоимость растёт быстрее запасов"),
        component("ccc_change", ccc_delta, [], "цикл оборота сокращается"),
        component("cash_conversion_change", conversion_delta, conversion_series,
                  "прибыль лучше превращается в деньги"),
        component("advance_ratio_change", advance_delta, [],
                  "доля авансов покупателей растёт"),
        component("gross_margin", margin_delta, [], "валовая маржа не ухудшается"),
    ]

    measured = [c for c in result.components if c.measured]
    if not measured:
        result.applicable = False
        result.reason_codes.append("wcq_no_measurable_components")
        result.detail = "ни один компонент не посчитан: раскрытия не хватает"
        return result

    # Вес отсутствующего компонента не перераспределяется (§13.1): падает
    # уверенность, а не растут остальные. Иначе одна раскрытая строка
    # давала бы такую же силу, как полный отчёт.
    total = sum(Decimal(str(c.weight)) * (c.value or Decimal(0)) for c in measured)
    result.strength = _q(total)
    result.confidence = _q(
        Decimal(str(sum(c.weight for c in measured)))
        * max(Decimal(0), result.data_quality)
    )

    direction, codes = _direction_rules(quarters, now, year_ago, revenue_growth)
    result.direction = direction
    result.reason_codes.extend(codes)

    # Рост DPO сам по себе не позитив: он может означать нехватку денег, а
    # не переговорную силу (§13.1, проверка 7).
    if (
        now.dpo is not None
        and year_ago.dpo is not None
        and now.dpo > year_ago.dpo * Decimal("1.15")
    ):
        result.reason_codes.append("wcq_dpo_stretch")

    result.metrics = {
        key: _q(value, "0.01")
        for key, value in {
            "dso": now.dso,
            "dio": now.dio,
            "dpo": now.dpo,
            "ccc": now.ccc,
            "ccc_year_ago": year_ago.ccc,
            "cash_conversion": now.cash_conversion,
            "revenue_growth": revenue_growth,
        }.items()
        if value is not None
    }
    result.detail = _detail(direction, now, year_ago, result.reason_codes)
    return result


def _detail(direction: str, now: Ratios, year_ago: Ratios, codes: list[str]) -> str:
    if direction == "positive":
        head = "рост обеспечен деньгами"
    elif direction == "negative":
        head = "прибыль растёт, денежный поток отстаёт"
    else:
        head = "изменений, отличимых от шума, нет"
    if now.ccc is not None and year_ago.ccc is not None:
        head += (
            f": цикл оборота {now.ccc:.0f} дней против {year_ago.ccc:.0f} "
            "годом ранее"
        )
    if "wcq_release_on_declining_sales" in codes:
        head += "; высвобождение связано с падением продаж, а не с управлением"
    if "wcq_factoring" in codes:
        head += "; в периоде есть факторинг — оборачиваемость завышена"
    return head


__all__ = [
    "DAYS",
    "EXCLUDED_SECTORS",
    "Component",
    "Evaluation",
    "MIN_QUARTERS_CONFIRMED",
    "MIN_QUARTERS_EARLY",
    "Quarter",
    "Ratios",
    "STRATEGY_KEY",
    "STRATEGY_VERSION",
    "WEIGHTS",
    "evaluate",
    "ratios_at",
]
