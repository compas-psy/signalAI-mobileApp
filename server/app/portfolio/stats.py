"""Статистическая основа портфеля (§6.2, §6.3).

Здесь нет ни одного готового веса и ни одной заготовки. Есть ряд доходностей
и способ превратить его в состав, который выдерживает проверку на данных, не
участвовавших в подборе.

Три вещи, из-за которых модуль устроен именно так.

**Ковариация по выборке непригодна.** У 20 инструментов и 250 наблюдений
матрица почти вырождена: оптимизатор находит «арбитраж» между двумя почти
одинаковыми бумагами и ставит туда весь капитал. Лечится сжатием к
структурированной цели (Ledoit–Wolf): интенсивность сжатия считается из
данных, а не назначается.

**Одна оценка средней доходности — это одна выборка из распределения.**
Оптимум по ней максимизирует ошибку оценки, а не доход. Отсюда ресэмплинг
(Michaud): состав пересчитывается на сотне бутстрэп-выборок и усредняется.
Усреднённый портфель хуже «оптимального» на исторических данных и лучше на
будущих — ровно тот размен, который нужен.

**Блочный бутстрэп, а не построчный.** Доходности активов связаны во времени
(кластеры волатильности) и между собой. Перемешивание по одной строке рвёт
только автокорреляцию, но не кросс-корреляцию, — поэтому блоки берутся
целыми строками подряд.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

# ── Выравнивание рядов ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ReturnPanel:
    """Панель доходностей: даты × инструменты, без пропусков.

    Пропуски не заполняются нулями. Ноль в ряду доходностей — это не «нет
    данных», а «цена не изменилась»; подстановка занижает волатильность и
    завышает вес инструмента, у которого просто нет истории.
    """

    dates: tuple[date, ...]
    columns: tuple[str, ...]
    values: np.ndarray  # (T, N), логарифмические доходности

    @property
    def periods(self) -> int:
        return int(self.values.shape[0])

    @property
    def width(self) -> int:
        return int(self.values.shape[1])

    def select(self, columns: Sequence[str]) -> ReturnPanel:
        index = [self.columns.index(c) for c in columns]
        return ReturnPanel(self.dates, tuple(columns), self.values[:, index])

    def window(self, start: int, stop: int) -> ReturnPanel:
        return ReturnPanel(self.dates[start:stop], self.columns, self.values[start:stop])


def build_panel(series: dict[str, list[tuple[date, float]]]) -> ReturnPanel:
    """Собрать панель по датам, общим для всех инструментов.

    Пересечение, а не объединение: оптимизатору нужна одновременная история,
    иначе ковариация считается по разным периодам для разных пар и перестаёт
    быть матрицей одного рынка.
    """
    usable = {k: v for k, v in series.items() if len(v) >= 2}
    if not usable:
        return ReturnPanel((), (), np.zeros((0, 0)))

    common: set[date] | None = None
    for points in usable.values():
        days = {d for d, _ in points}
        common = days if common is None else (common & days)
    dates = sorted(common or set())
    if len(dates) < 2:
        return ReturnPanel((), (), np.zeros((0, 0)))

    columns = sorted(usable)
    prices = np.empty((len(dates), len(columns)), dtype=float)
    for j, key in enumerate(columns):
        by_day = dict(usable[key])
        for i, day in enumerate(dates):
            prices[i, j] = by_day[day]

    if np.any(prices <= 0):
        # Нулевая или отрицательная цена — не доходность, а испорченный ряд.
        # Логарифм от неё даёт inf и заражает всю матрицу.
        keep = [j for j in range(prices.shape[1]) if np.all(prices[:, j] > 0)]
        prices = prices[:, keep]
        columns = [columns[j] for j in keep]
        if not columns:
            return ReturnPanel((), (), np.zeros((0, 0)))

    returns = np.diff(np.log(prices), axis=0)
    return ReturnPanel(tuple(dates[1:]), tuple(columns), returns)


# ── Оценки моментов ──────────────────────────────────────────────────────


def shrunk_covariance(returns: np.ndarray) -> np.ndarray:
    """Ковариация со сжатием к цели «одинаковая корреляция».

    Цель выбрана не диагональная: активы одного рынка коррелируют, и
    обнуление всех связей — такая же неправда, как выборочная матрица с её
    ложными связями. Интенсивность сжатия оценивается по формуле Ledoit–Wolf
    (доля дисперсии оценок в общем расхождении с целью) и зажимается в [0, 1].
    """
    T, N = returns.shape
    if T < 2 or N == 0:
        return np.zeros((N, N))
    centred = returns - returns.mean(axis=0, keepdims=True)
    sample = centred.T @ centred / T

    variances = np.diag(sample).copy()
    variances[variances <= 0] = 1e-12
    sd = np.sqrt(variances)
    outer = np.outer(sd, sd)
    corr = sample / outer
    off = corr[~np.eye(N, dtype=bool)]
    mean_corr = float(off.mean()) if off.size else 0.0
    target = mean_corr * outer
    np.fill_diagonal(target, variances)

    # Дисперсия оценок элементов ковариации.
    squared = (centred**2).T @ (centred**2) / T
    pi_matrix = squared - sample**2
    pi = float(pi_matrix.sum())
    rho = float(np.trace(pi_matrix))  # диагональ цели совпадает с выборкой
    gamma = float(((target - sample) ** 2).sum())
    if gamma <= 0:
        return sample
    intensity = max(0.0, min(1.0, (pi - rho) / gamma / max(T, 1)))
    return intensity * target + (1 - intensity) * sample


def annualise_mean(returns: np.ndarray, periods_per_year: int = 252) -> np.ndarray:
    return returns.mean(axis=0) * periods_per_year


def shrunk_mean(
    returns: np.ndarray, cov: np.ndarray, *, periods_per_year: int = 252
) -> np.ndarray:
    """Ожидаемая доходность со сжатием к портфелю минимальной дисперсии.

    Оценка Джориона (Bayes–Stein, 1986). Ковариацию мы сжимали с самого
    начала, а среднее брали выборочным — и это была не мелочь, а
    несимметричность в самом чувствительном месте. Выборочное среднее —
    худший вход оптимизации: его стандартная ошибка убывает как корень из
    длины окна, и на двух годах дневных данных разница «12% годовых» и «2%
    годовых» статистически неразличима. Оптимизатор же принимает её за
    факт и уводит вес туда, где среднее случайно оказалось выше.

    Цель сжатия — доходность портфеля минимальной дисперсии, а не ноль и не
    среднее по бумагам. Ноль был бы утверждением «все активы бесперспективны»,
    среднее по бумагам зависит от того, сколько каких бумаг попало в отбор.
    Портфель минимальной дисперсии — единственная точка, которую данные
    задают сами и которая не зависит от состава выборки.

    Интенсивность не подбирается: она выводится из данных. Чем больше бумаг
    и короче окно — тем сильнее сжатие; чем сильнее бумаги разошлись
    относительно ошибки оценки — тем слабее. Порядок бумаг сжатие сохраняет:
    это выпуклая комбинация с общей точкой, и бумага, которая была выше
    другой, выше и останется.
    """
    T, N = returns.shape
    mu = annualise_mean(returns, periods_per_year)
    if T < 2 or N == 0:
        return mu
    inv = np.linalg.pinv(cov)
    ones = np.ones(N)
    denominator = float(ones @ inv @ ones)
    if not np.isfinite(denominator) or abs(denominator) < 1e-12:
        return mu
    # Доходность портфеля минимальной дисперсии — точка, к которой тянем.
    grand = float(ones @ inv @ mu) / denominator
    diff = mu - grand
    # Квадратичная форма приводится к единицам одного периода. И среднее, и
    # ковариация здесь годовые, а `T` считает дни: годовое расхождение,
    # делённое на годовую дисперсию, даёт величину в 252 раза больше дневной,
    # и сжатие обнулялось на любых данных. Ошибка была тихой — формула
    # работала, просто всегда возвращала выборочное среднее.
    spread = float(diff @ inv @ diff) / periods_per_year
    if not np.isfinite(spread) or spread <= 0:
        return np.full(N, grand)
    intensity = (N + 2) / ((N + 2) + T * spread)
    intensity = float(np.clip(intensity, 0.0, 1.0))
    return (1.0 - intensity) * mu + intensity * grand


def annualise_cov(cov: np.ndarray, periods_per_year: int = 252) -> np.ndarray:
    return cov * periods_per_year


# ── Проекции ─────────────────────────────────────────────────────────────


def project_box_simplex(
    v: np.ndarray, lo: np.ndarray, hi: np.ndarray, *, total: float = 1.0
) -> np.ndarray:
    """Ближайшая точка множества {сумма = total, lo ≤ w ≤ hi}.

    Решение имеет вид clip(v − λ, lo, hi); сумма по λ монотонно убывает,
    поэтому λ находится делением отрезка пополам. Точное решение, а не
    «нормировать и обрезать»: нормировка после обрезки снова выводит за
    границы и в цикле не сходится.
    """
    if v.size == 0:
        return v
    if lo.sum() > total + 1e-9 or hi.sum() < total - 1e-9:
        # Ограничения несовместимы с требуемой суммой. Молча вернуть что-то
        # похожее нельзя: состав перестанет быть портфелем.
        raise ValueError("границы весов не допускают требуемой суммы")
    low = float((v - hi).min())
    high = float((v - lo).max())
    for _ in range(60):
        mid = (low + high) / 2
        clipped = np.clip(v - mid, lo, hi)
        current = float(clipped.sum())
        if abs(current - total) < 1e-12:
            return clipped
        if current > total:
            low = mid
        else:
            high = mid
    return np.clip(v - (low + high) / 2, lo, hi)


def project_capped(
    v: np.ndarray,
    lo: np.ndarray,
    hi: np.ndarray,
    groups: Sequence[tuple[np.ndarray, float]],
) -> np.ndarray:
    """Точная проекция на бокс-симплекс с потолками по **непересекающимся** классам.

    Работает разбором активного набора, и это не оптимизация ради скорости, а
    следствие структуры задачи. Если известно, какие потолки упёрлись, задача
    распадается: каждый упёршийся класс получает ровно свой потолок, остаток
    делится между остальными бумагами. Блоки не пересекаются, значит каждый
    проецируется отдельно — четыре дешёвых проекции вместо тысяч витков
    поочерёдных приближений.

    Активный набор находится за несколько проходов: сначала проверяется
    решение без потолков, затем добавляются нарушенные. Проходов не больше,
    чем классов, потому что снятым потолок уже не становится — освободить его
    может только рост остальных, а они здесь только уменьшаются.

    Классы обязаны быть непересекающимися: у бумаги один класс актива. Если
    это перестанет быть правдой, разбор надо будет менять — поэтому проверка
    стоит здесь, а не в комментарии.
    """
    plain = project_box_simplex(v, lo, hi)
    if not groups:
        return plain

    # Issuer-cap может быть вложен в группу класса акций. Активный разбор
    # ниже корректен только для непересекающихся групп; для пересечений
    # project_with_groups использует общий алгоритм Дейкстры.
    occupied = np.zeros(v.size, dtype=bool)
    for mask, _ in groups:
        if np.any(occupied & mask):
            raise ValueError("пересекающиеся группы требуют общей проекции")
        occupied |= mask

    binding: list[tuple[np.ndarray, float]] = []
    result = plain
    for _ in range(len(groups) + 1):
        fresh = [
            (mask, cap)
            for mask, cap in groups
            if not any(m is mask for m, _ in binding)
            and float(result[mask].sum()) > cap + 1e-9
        ]
        if not fresh:
            return result
        binding.extend(fresh)

        used = np.zeros(v.size, dtype=bool)
        for mask, _ in binding:
            used |= mask
        rest_total = 1.0 - sum(cap for _, cap in binding)
        if rest_total < -1e-9:
            # Потолки в сумме меньше единицы даже вместе — набрать 100%
            # нечем. Пусть решает вызывающий: здесь честный отказ.
            raise ValueError("потолки классов не допускают суммы 1")
        candidate = np.empty_like(v)
        for mask, cap in binding:
            candidate[mask] = project_box_simplex(
                v[mask], lo[mask], hi[mask], total=cap
            )
        others = ~used
        if others.any():
            candidate[others] = project_box_simplex(
                v[others], lo[others], hi[others], total=max(0.0, rest_total)
            )
        elif abs(rest_total) > 1e-9:
            raise ValueError("потолки классов не допускают суммы 1")
        result = candidate
    return result


def project_with_groups(
    v: np.ndarray,
    lo: np.ndarray,
    hi: np.ndarray,
    groups: Sequence[tuple[np.ndarray, float]],
    *,
    iterations: int = 60,
) -> np.ndarray:
    """Проекция на пересечение бокса-симплекса и потолков по классам.

    Основной путь — точный разбор активного набора (`project_capped`): классы
    активов не пересекаются, и задача распадается на независимые блоки.
    Алгоритм Дейкстры остаётся запасным на случай, когда разбор невыполним:
    поочерёдная проекция на каждое множество с поправкой на накопленное
    смещение. Простое чередование сходится не к проекции, а к какой-то точке
    пересечения — поправки Дейкстры это исправляют.
    """
    if not groups:
        return project_box_simplex(v, lo, hi)
    try:
        return project_capped(v, lo, hi, groups)
    except ValueError:
        pass
    x = v.copy()
    corrections = [np.zeros_like(v) for _ in range(len(groups) + 1)]
    for _ in range(iterations):
        previous_x = x.copy()
        previous_corrections = [c.copy() for c in corrections]
        y = x + corrections[0]
        x = project_box_simplex(y, lo, hi)
        corrections[0] = y - x
        for k, (mask, cap) in enumerate(groups, start=1):
            y = x + corrections[k]
            total = float(y[mask].sum())
            z = y.copy()
            if total > cap and int(mask.sum()) > 0:
                z[mask] = y[mask] - (total - cap) / float(mask.sum())
            x = z
            corrections[k] = y - x
        # У пересекающихся ограничений x может на несколько циклов
        # стабилизироваться, пока продолжают меняться поправки Дейкстры.
        # Остановка только по x завершала проекцию с суммой весов < 1.
        x_delta = float(np.abs(x - previous_x).max())
        correction_delta = max(
            float(np.abs(current - previous).max())
            for current, previous in zip(corrections, previous_corrections)
        )
        if x_delta < 1e-10 and correction_delta < 1e-10:
            break
    return x


# ── Оптимизация ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Constraints:
    """Границы состава. Всё, что ограничивает оптимизатор, — здесь."""

    lo: np.ndarray
    hi: np.ndarray
    groups: tuple[tuple[np.ndarray, float], ...] = ()

    @property
    def feasible(self) -> bool:
        if self.lo.size == 0:
            return False
        if self.lo.sum() > 1.0 + 1e-9 or self.hi.sum() < 1.0 - 1e-9:
            return False
        return sum(cap for _, cap in self.groups) >= 1.0 - 1e-9 or not self.groups


def _maximise(
    mu: np.ndarray,
    cov: np.ndarray,
    risk_aversion: float,
    c: Constraints,
    *,
    iterations: int = 250,
    start: np.ndarray | None = None,
) -> np.ndarray:
    """Максимум mu'w − (λ/2)·w'Σw при ограничениях.

    Ускоренный проекционный градиент (FISTA): та же задача, что у простого
    проекционного спуска, но сходится за десятки шагов вместо сотен. Здесь
    это не микрооптимизация — метод вызывается сто с лишним раз на каждый
    пакет, и на простом спуске один пакет считался минутами.

    Шаг обратен наибольшему собственному числу λΣ: гарантирует сходимость
    без подбора скорости.
    """
    n = mu.size
    hessian = risk_aversion * cov
    eig = float(np.linalg.eigvalsh(hessian).max()) if n else 0.0
    step = 1.0 / eig if eig > 1e-12 else 1.0
    seed = np.full(n, 1.0 / n) if start is None else start
    w = project_with_groups(seed, c.lo, c.hi, c.groups)
    y = w.copy()
    t = 1.0
    for _ in range(iterations):
        nxt = project_with_groups(y + step * (mu - hessian @ y), c.lo, c.hi, c.groups)
        t_next = (1 + (1 + 4 * t * t) ** 0.5) / 2
        y = nxt + ((t - 1) / t_next) * (nxt - w)
        done = float(np.abs(nxt - w).max()) < 1e-9
        w, t = nxt, t_next
        if done:
            break
    return w


def risk_aversion_for(
    mu: np.ndarray,
    cov: np.ndarray,
    constraints: Constraints,
    *,
    target_volatility: float,
    steps: int = 24,
) -> float:
    """Неприятие риска, при котором состав колеблется на целевую величину.

    Целевая волатильность — это и есть профиль риска: «консервативный»
    отличается от «агрессивного» не набором фондов, а тем, сколько колебаний
    владелец согласен переносить. Возвращается параметр, а не веса: в
    ресэмплинге он подбирается один раз по всей выборке и потом одинаково
    применяется к каждой бутстрэп-выборке. Иначе усреднялись бы портфели
    разных мест на границе эффективности — то есть разные по смыслу.
    """
    low, high = 1e-3, 1e6
    if _volatility(_maximise(mu, cov, high, constraints), cov) > target_volatility:
        return high  # тише уже не сделать — ограничения не пускают
    for _ in range(steps):
        mid = (low * high) ** 0.5
        if _volatility(_maximise(mu, cov, mid, constraints), cov) > target_volatility:
            low = mid
        else:
            high = mid
    return high


def optimise_to_volatility(
    mu: np.ndarray,
    cov: np.ndarray,
    constraints: Constraints,
    *,
    target_volatility: float,
) -> np.ndarray:
    """Состав с волатильностью не выше целевой."""
    aversion = risk_aversion_for(
        mu, cov, constraints, target_volatility=target_volatility
    )
    return _maximise(mu, cov, aversion, constraints)


def _volatility(w: np.ndarray, cov: np.ndarray) -> float:
    return float(np.sqrt(max(0.0, w @ cov @ w)))


# ── Ресэмплинг ───────────────────────────────────────────────────────────


def block_bootstrap(
    returns: np.ndarray, rng: np.random.Generator, *, block: int
) -> np.ndarray:
    """Выборка целыми блоками строк.

    Блок берётся по кругу (circular block bootstrap): иначе хвост ряда
    попадает в выборки реже начала, и последние месяцы — самые важные для
    оценки текущего режима — систематически недопредставлены.
    """
    T = returns.shape[0]
    if T == 0:
        return returns
    size = max(1, min(block, T))
    count = int(np.ceil(T / size))
    starts = rng.integers(0, T, size=count)
    index = np.concatenate([(np.arange(size) + s) % T for s in starts])[:T]
    return returns[index]


@dataclass(frozen=True, slots=True)
class ResampleResult:
    weights: np.ndarray
    dispersion: np.ndarray  # СКО веса по выборкам — устойчивость позиции
    draws: int


def resampled_weights(
    returns: np.ndarray,
    constraints: Constraints,
    *,
    target_volatility: float,
    draws: int = 120,
    block: int = 21,
    seed: int = 20260730,
    periods_per_year: int = 252,
) -> ResampleResult:
    """Усреднённый состав по бутстрэп-выборкам (Michaud resampling).

    Возвращается не только среднее, но и разброс веса по выборкам. Разброс —
    это честная мера «насколько эта позиция вообще устойчива»: инструмент,
    вес которого скачет от нуля до потолка, в пакет попадать не должен, даже
    если в среднем выглядит прилично.
    """
    rng = np.random.default_rng(seed)
    n = returns.shape[1]
    if n == 0 or returns.shape[0] < 30:
        return ResampleResult(np.zeros(n), np.zeros(n), 0)

    # Место на границе эффективности выбирается один раз — по всей выборке.
    # Подбирать его заново внутри каждой бутстрэп-выборки значило бы
    # усреднять портфели разного риска, а Michaud усредняет одинаковые.
    try:
        full_cov = annualise_cov(shrunk_covariance(returns), periods_per_year)
        aversion = risk_aversion_for(
            shrunk_mean(returns, full_cov, periods_per_year=periods_per_year),
            full_cov,
            constraints,
            target_volatility=target_volatility,
        )
    except ValueError:
        return ResampleResult(np.zeros(n), np.zeros(n), 0)

    collected = np.empty((draws, n))
    ok = 0
    warm: np.ndarray | None = None
    for _ in range(draws):
        sample = block_bootstrap(returns, rng, block=block)
        cov = annualise_cov(shrunk_covariance(sample), periods_per_year)
        # Сжатие внутри каждой выборки, а не один раз снаружи: бутстрэп для
        # того и нужен, чтобы показать, насколько оценка гуляет, — а гуляет
        # именно она, среднее по выборке.
        mu = shrunk_mean(sample, cov, periods_per_year=periods_per_year)
        try:
            # Тёплый старт с прошлой выборки: соседние решения близки, и
            # спуск сходится за единицы шагов вместо десятков.
            collected[ok] = _maximise(mu, cov, aversion, constraints, start=warm)
        except ValueError:
            continue
        warm = collected[ok]
        ok += 1
    if ok == 0:
        return ResampleResult(np.zeros(n), np.zeros(n), 0)
    used = collected[:ok]
    mean = used.mean(axis=0)
    # Усреднение по выборкам может слегка нарушить сумму из-за отброшенных
    # решений — возвращаем на множество ограничений, а не нормируем делением.
    mean = project_with_groups(mean, constraints.lo, constraints.hi, constraints.groups)
    return ResampleResult(mean, used.std(axis=0), ok)


# ── Метрики результата ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Performance:
    """Что получилось на ряде доходностей портфеля."""

    periods: int
    cagr: float
    volatility: float
    max_drawdown: float
    cvar_95: float
    positive_share: float

    @property
    def sharpe(self) -> float:
        return self.cagr / self.volatility if self.volatility > 1e-9 else 0.0


def performance(series: np.ndarray, *, periods_per_year: int = 252) -> Performance:
    """Метрики по ряду логарифмических доходностей портфеля."""
    if series.size == 0:
        return Performance(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    equity = np.exp(np.cumsum(series))
    peak = np.maximum.accumulate(equity)
    drawdown = float((1 - equity / peak).max())
    years = series.size / periods_per_year
    cagr = float(equity[-1] ** (1 / years) - 1) if years > 0 else 0.0
    vol = float(series.std(ddof=1) * np.sqrt(periods_per_year)) if series.size > 1 else 0.0
    tail = np.sort(series)[: max(1, int(series.size * 0.05))]
    cvar = float(-tail.mean() * np.sqrt(periods_per_year))
    positive = float((series > 0).mean())
    return Performance(int(series.size), cagr, vol, drawdown, cvar, positive)
