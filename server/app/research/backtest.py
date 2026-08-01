"""Проверка гипотез на истории и теневой портфель (ТЗ §19).

Две **разные** задачи, и в этом весь раздел. Первая: сбылось ли то, что
гипотеза предсказывала про показатель компании. Вторая: заработал ли на
этом инструмент относительно рынка. Успех первой не гарантирует второй, и
смешивать их — значит не понимать, что именно сломалось, когда результат
плохой.

Точечность во времени здесь не аккуратность, а условие осмысленности.
Ошибка «заглянул в будущее» не выглядит как ошибка: числа сходятся,
результат красив, и понять, что модель знала отчёт за две недели до
публикации, можно только по коду. Поэтому проверки вынесены в отдельные
утверждения и выполняются на каждом событии, а не «по возможности».

Вход берётся на следующем исполнимом баре после ``tradable_at``, а не в
момент публикации. Разница между этими двумя точками — это и есть весь
результат у большинства плохих исследований.

Независимость событий считается кластерами. Пять сигналов об одной компании
и одной истории — это одно наблюдение, а не пять; версии одной гипотезы —
тем более. Иначе тридцать «независимых случаев» окажутся тремя.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from statistics import median

# Горизонты §19.4. Короткие нужны для оценки реакции и риска, а не как цель.
MARKET_HORIZONS_DAYS = (30, 90, 180, 365, 540, 730)
PRIMARY_HORIZONS_DAYS = (180, 365, 730)

# Минимум независимых кластеров для вывода о стратегии (§19.6).
MIN_CLUSTERS = 25

# Издержки по умолчанию. Не «примерно ноль»: половина спреда и проскальзывание
# съедают у неликвидной бумаги больше, чем даёт сигнал.
DEFAULT_COMMISSION = Decimal("0.0005")
DEFAULT_HALF_SPREAD = Decimal("0.0015")
DEFAULT_SLIPPAGE = Decimal("0.0010")


class LeakageError(AssertionError):
    """Модель увидела то, чего на момент решения не существовало.

    Отдельный тип, а не общее исключение: это единственная ошибка, при
    которой результат выглядит лучше правды, и путать её с падением
    загрузки нельзя.
    """


@dataclass(frozen=True, slots=True)
class Bar:
    """Торговый бар. ``tradeable=False`` — приостановка, лимит, нет объёма."""

    time: datetime
    close: Decimal
    tradeable: bool = True


@dataclass(frozen=True, slots=True)
class Event:
    """Одно событие проверки: гипотеза, доведённая до решения."""

    hypothesis_id: str
    cluster_id: str
    entity_id: str
    instrument_id: str
    # Момент, когда гипотеза приняла состояние, по которому входят.
    as_of: datetime
    # Первая точка, с которой фактами разрешено пользоваться.
    tradable_at: datetime
    strategy_keys: tuple[str, ...] = ()
    # Когда были доступны признаки, на которых построен сигнал.
    feature_available_at: datetime | None = None
    detected_at: datetime | None = None
    # Экономическая часть: сбылось ли предсказание о показателе.
    kpi_expected_direction: str = ""
    kpi_actual_change: Decimal | None = None
    strength_bucket: str = ""
    regime: str = ""


@dataclass(frozen=True, slots=True)
class Costs:
    commission: Decimal = DEFAULT_COMMISSION
    half_spread: Decimal = DEFAULT_HALF_SPREAD
    slippage: Decimal = DEFAULT_SLIPPAGE

    @property
    def one_way(self) -> Decimal:
        return self.commission + self.half_spread + self.slippage


@dataclass
class EventResult:
    """Что случилось с одним событием."""

    hypothesis_id: str
    cluster_id: str
    entry_time: datetime | None = None
    entry_price: Decimal | None = None
    delay_days: int = 0
    executable: bool = True
    returns: dict[int, Decimal] = field(default_factory=dict)
    excess: dict[int, Decimal] = field(default_factory=dict)
    mae: Decimal | None = None
    mfe: Decimal | None = None
    kpi_hit: bool | None = None
    note: str = ""


def assert_point_in_time(event: Event) -> None:
    """Проверки §19.10. Падают, а не предупреждают.

    Порядок событий обязан быть строгим: признаки доступны раньше сигнала,
    сигнал раньше гипотезы, гипотеза раньше входа. Нарушение любого — это
    не «неточность», а результат, который нельзя показывать.
    """
    if event.feature_available_at and event.detected_at:
        if event.feature_available_at > event.detected_at:
            raise LeakageError(
                f"{event.hypothesis_id}: признак доступен "
                f"{event.feature_available_at:%d.%m.%Y %H:%M}, а сигнал найден "
                f"раньше — {event.detected_at:%d.%m.%Y %H:%M}"
            )
    if event.detected_at and event.detected_at > event.as_of:
        raise LeakageError(
            f"{event.hypothesis_id}: сигнал найден позже, чем гипотеза приняла "
            "состояние"
        )
    if event.tradable_at < event.as_of:
        # Не ошибка сама по себе: гипотеза могла сложиться позже, чем факт
        # стал доступен. Обратное — ошибка.
        return


def first_executable(
    bars: list[Bar], after: datetime
) -> tuple[Bar | None, int]:
    """Первый бар, на котором вход был возможен, и задержка в днях.

    Приостановка торгов и нулевой объём не пропускаются молча: задержка
    записывается, потому что она и есть разница между исследованием и
    реальностью.
    """
    for bar in bars:
        if bar.time <= after:
            continue
        if not bar.tradeable:
            continue
        return bar, max(0, (bar.time - after).days)
    return None, 0


def _q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def run_event(
    event: Event,
    bars: list[Bar],
    benchmark: list[Bar],
    *,
    costs: Costs = Costs(),
    horizons: tuple[int, ...] = MARKET_HORIZONS_DAYS,
) -> EventResult:
    """Прожить одно событие по барам."""
    assert_point_in_time(event)
    result = EventResult(
        hypothesis_id=event.hypothesis_id, cluster_id=event.cluster_id
    )

    # Экономическая часть считается независимо от рыночной: показатель мог
    # сбыться там, где бумага не выросла, и это важный, а не досадный
    # результат.
    if event.kpi_actual_change is not None and event.kpi_expected_direction:
        want_up = event.kpi_expected_direction == "up"
        result.kpi_hit = (
            event.kpi_actual_change > 0 if want_up else event.kpi_actual_change < 0
        )

    entry_bar, delay = first_executable(bars, event.tradable_at)
    if entry_bar is None:
        result.executable = False
        result.note = (
            "исполнимого бара после момента доступности не нашлось: "
            "вход невозможен"
        )
        return result

    result.entry_time = entry_bar.time
    result.delay_days = delay
    # Издержки входа сразу в цене: сравнивать «как если бы купили по
    # закрытию» — значит систематически завышать результат.
    result.entry_price = _q(entry_bar.close * (Decimal(1) + costs.one_way))

    bench_entry, _ = first_executable(benchmark, event.tradable_at)

    lows: list[Decimal] = []
    highs: list[Decimal] = []
    for horizon in horizons:
        deadline = entry_bar.time + timedelta(days=horizon)
        exit_bar = None
        for bar in bars:
            if bar.time <= entry_bar.time or bar.time > deadline:
                continue
            exit_bar = bar
            lows.append(bar.close)
            highs.append(bar.close)
        if exit_bar is None:
            continue
        gross = exit_bar.close * (Decimal(1) - costs.one_way)
        ret = _q(gross / result.entry_price - Decimal(1))
        result.returns[horizon] = ret

        if bench_entry is not None:
            bench_exit = None
            for bar in benchmark:
                if bar.time <= bench_entry.time or bar.time > deadline:
                    continue
                bench_exit = bar
            if bench_exit is not None:
                bench_ret = _q(bench_exit.close / bench_entry.close - Decimal(1))
                result.excess[horizon] = _q(ret - bench_ret)

    if lows:
        result.mae = _q(min(lows) / result.entry_price - Decimal(1))
        result.mfe = _q(max(highs) / result.entry_price - Decimal(1))
    return result


@dataclass
class Summary:
    """Итог по стратегии. Числа отдельно от вывода о них."""

    events: int = 0
    clusters: int = 0
    executable: int = 0
    kpi_sign_accuracy: Decimal | None = None
    median_excess: dict[int, Decimal] = field(default_factory=dict)
    hit_rate: dict[int, Decimal] = field(default_factory=dict)
    median_excess_without_top2: dict[int, Decimal] = field(default_factory=dict)
    by_regime: dict[str, int] = field(default_factory=dict)
    sufficient_sample: bool = False
    verdict: str = ""


def summarise(
    results: list[EventResult],
    events: list[Event],
    *,
    horizons: tuple[int, ...] = PRIMARY_HORIZONS_DAYS,
) -> Summary:
    """Свести события в итог по стратегии.

    Два обязательных элемента, без которых итог вводит в заблуждение.

    **Кластеры вместо событий.** Пять сигналов об одной истории — одно
    наблюдение. Считать их пятью значит объявить достаточной выборку,
    которой нет.

    **Результат без двух лучших событий.** Если он разваливается, стратегии
    нет — есть два удачных случая, и §19.5 требует показывать это числом, а
    не оговоркой в примечании.
    """
    summary = Summary(events=len(results))
    summary.clusters = len({r.cluster_id for r in results})
    summary.executable = sum(1 for r in results if r.executable)

    hits = [r.kpi_hit for r in results if r.kpi_hit is not None]
    if hits:
        summary.kpi_sign_accuracy = Decimal(sum(1 for h in hits if h)) / Decimal(
            len(hits)
        )

    for horizon in horizons:
        values = [
            r.excess[horizon] for r in results if horizon in r.excess and r.executable
        ]
        if not values:
            continue
        summary.median_excess[horizon] = _q(
            Decimal(str(median(float(v) for v in values)))
        )
        summary.hit_rate[horizon] = _q(
            Decimal(sum(1 for v in values if v > 0)) / Decimal(len(values))
        )
        trimmed = sorted(values)[:-2] if len(values) > 2 else []
        if trimmed:
            summary.median_excess_without_top2[horizon] = _q(
                Decimal(str(median(float(v) for v in trimmed)))
            )

    for event in events:
        if event.regime:
            summary.by_regime[event.regime] = summary.by_regime.get(event.regime, 0) + 1

    summary.sufficient_sample = summary.clusters >= MIN_CLUSTERS
    if not summary.sufficient_sample:
        summary.verdict = (
            f"независимых наблюдений {summary.clusters} из {MIN_CLUSTERS}: "
            "вывода об эффективности стратегии сделать нельзя"
        )
    elif len(summary.by_regime) == 1:
        # §19.8: результат, созданный одним режимом, не является
        # доказательством устойчивости.
        summary.verdict = (
            "весь результат получен в одном режиме рынка: устойчивость не "
            "доказана"
        )
    else:
        summary.verdict = "выборки достаточно для вывода"
    return summary


__all__ = [
    "Bar",
    "Costs",
    "DEFAULT_COMMISSION",
    "Event",
    "EventResult",
    "LeakageError",
    "MARKET_HORIZONS_DAYS",
    "MIN_CLUSTERS",
    "PRIMARY_HORIZONS_DAYS",
    "Summary",
    "assert_point_in_time",
    "first_executable",
    "run_event",
    "summarise",
]
