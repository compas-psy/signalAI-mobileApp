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


@dataclass(frozen=True, slots=True)
class ReturnPanel:
    """Панель доходностей: даты × инструменты, без пропусков."""

    dates: tuple[date, ...]
    columns: tuple[str, ...]
    values: np.ndarray

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
        keep = [j for j in range(prices.shape[1]) if np.all(prices[:, j] > 0)]
        prices = prices[:, keep]
        columns = [columns[j] for j in keep]
        if not columns:
            return ReturnPanel((), (), np.zeros((0, 0)))

    returns = np.diff(np.log(prices), axis=0)
    return ReturnPanel(tuple(dates[1:]), tuple(columns), returns)


def shrunk_covariance(returns: np.ndarray) -> np.ndarray:
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

    squared = (centred**2).T @ (centred**2) / T
    pi_matrix = squared - sample**2
    pi = float(pi_matrix.sum())
    rho = float(np.trace(pi_matrix))
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
    T, N = returns.shape
    mu = annualise_mean(returns, periods_per_year)
    if T < 2 or N == 0:
        return mu
    inv = np.linalg.pinv(cov)
    ones = np.ones(N)
    denominator = float(ones @ inv @ ones)
    if not np.isfinite(denominator) or abs(denominator) < 1e-12:
        return mu
    grand = float(ones @ inv @ mu) / denominator
    diff = mu - grand
    spread = float(diff @ inv @ diff) / periods_per_year
    if not np.isfinite(spread) or spread <= 0:
        return np.full(N, grand)
    intensity = (N + 2) / ((N + 2) + T * spread)
    intensity = float(np.clip(intensity, 0.0, 1.0))
    return (1.0 - intensity) * mu + intensity * grand


def annualise_cov(cov: np.ndarray, periods_per_year: int = 252) -> np.ndarray:
    return cov * periods_per_year


def project_box_simplex(
    v: np.ndarray, lo: np.ndarray, hi: np.ndarray, *, total: float = 1.0
) -> np.ndarray:
    if v.size == 0:
        return v
    if lo.sum() > total + 1e-9 or hi.sum() < total - 1e-9:
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
    """Точная проекция для непересекающихся групп; пересечения идут в Dykstra."""
    plain = project_box_simplex(v, lo, hi)
    if not groups:
        return plain

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
        x_delta = float(np.abs(x - previous_x).max())
        correction_delta = max(
            float(np.abs(current - previous).max())
            for current, previous in zip(corrections, previous_corrections)
        )
        if x_delta < 1e-10 and correction_delta < 1e-10:
            break
    return x


@dataclass(frozen=True, slots=True)
class Constraints:
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
    low, high = 1e-3, 1e6
    if _volatility(_maximise(mu, cov, high, constraints), cov) > target_volatility:
        return high
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
    aversion = risk_aversion_for(
        mu, cov, constraints, target_volatility=target_volatility
    )
    return _maximise(mu, cov, aversion, constraints)


def _volatility(w: np.ndarray, cov: np.ndarray) -> float:
    return float(np.sqrt(max(0.0, w @ cov @ w)))


def block_bootstrap(
    returns: np.ndarray, rng: np.random.Generator, *, block: int
) -> np.ndarray:
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
    dispersion: np.ndarray
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
    rng = np.random.default_rng(seed)
    n = returns.shape[1]
    if n == 0 or returns.shape[0] < 30:
        return ResampleResult(np.zeros(n), np.zeros(n), 0)

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
        mu = shrunk_mean(sample, cov, periods_per_year=periods_per_year)
        try:
            collected[ok] = _maximise(mu, cov, aversion, constraints, start=warm)
        except ValueError:
            continue
        warm = collected[ok]
        ok += 1
    if ok == 0:
        return ResampleResult(np.zeros(n), np.zeros(n), 0)
    used = collected[:ok]
    mean = used.mean(axis=0)
    mean = project_with_groups(mean, constraints.lo, constraints.hi, constraints.groups)
    return ResampleResult(mean, used.std(axis=0), ok)


@dataclass(frozen=True, slots=True)
class Performance:
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
