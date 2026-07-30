"""Математика портфеля — без базы.

Проверяется не «функция что-то вернула», а свойства, ради которых модуль
написан: проекция действительно попадает в допустимое множество, потолки
классов соблюдаются, оптимум не превышает целевых колебаний, скользящая
проверка не подглядывает в будущее.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from app.portfolio.stats import (
    Constraints,
    ReturnPanel,
    block_bootstrap,
    build_panel,
    optimise_to_volatility,
    performance,
    project_box_simplex,
    project_capped,
    project_with_groups,
    resampled_weights,
    shrunk_covariance,
)
from app.portfolio.walkforward import judge, walk_forward


def _panel(periods: int = 800, width: int = 6, seed: int = 7) -> ReturnPanel:
    rng = np.random.default_rng(seed)
    # Один общий фактор плюс собственный шум: так выглядит настоящий рынок,
    # и именно на таких данных выборочная ковариация вырождается.
    factor = rng.normal(0, 0.011, size=periods)
    loadings = np.linspace(0.3, 1.4, width)
    noise = rng.normal(0, 0.006, size=(periods, width))
    drift = np.linspace(0.0001, 0.0006, width)
    values = drift + np.outer(factor, loadings) + noise
    start = date(2020, 1, 1)
    dates = tuple(start + timedelta(days=i) for i in range(periods))
    columns = tuple(f"MOEX:EQ:A{i}" for i in range(width))
    return ReturnPanel(dates, columns, values)


def test_панель_строится_по_общим_датам() -> None:
    a = [(date(2024, 1, i), 100.0 + i) for i in range(1, 6)]
    b = [(date(2024, 1, i), 50.0 + i) for i in range(3, 8)]
    panel = build_panel({"A": a, "B": b})
    # Пересечение — 3, 4, 5 января; доходностей на день меньше, чем дат.
    assert panel.columns == ("A", "B")
    assert panel.periods == 2
    assert panel.dates == (date(2024, 1, 4), date(2024, 1, 5))


def test_ряд_с_нулевой_ценой_выбрасывается_целиком() -> None:
    good = [(date(2024, 1, i), 100.0 + i) for i in range(1, 6)]
    broken = [(date(2024, 1, i), 0.0) for i in range(1, 6)]
    panel = build_panel({"A": good, "B": broken})
    assert panel.columns == ("A",)


def test_проекция_попадает_в_бокс_и_даёт_единицу() -> None:
    v = np.array([0.9, -0.4, 0.2, 0.5])
    lo = np.zeros(4)
    hi = np.array([0.3, 0.3, 0.3, 0.3])
    w = project_box_simplex(v, lo, hi)
    assert w.sum() == pytest.approx(1.0, abs=1e-9)
    assert (w >= -1e-12).all() and (w <= hi + 1e-12).all()


def test_несовместимые_границы_отказ_а_не_молчание() -> None:
    with pytest.raises(ValueError):
        project_box_simplex(np.zeros(3), np.zeros(3), np.full(3, 0.2))


def test_потолок_класса_соблюдается() -> None:
    v = np.array([0.8, 0.7, 0.1, 0.1])
    lo, hi = np.zeros(4), np.full(4, 0.6)
    crypto = np.array([True, True, False, False])
    w = project_with_groups(v, lo, hi, [(crypto, 0.05)])
    assert w.sum() == pytest.approx(1.0, abs=1e-8)
    assert w[crypto].sum() <= 0.05 + 1e-8


def test_точная_проекция_совпадает_с_поочерёдной() -> None:
    """Разбор активного набора обязан давать ту же точку, что и Дейкстра.

    Быстрый путь заменил медленный, и «быстрее» не оправдание, если ответ
    другой: от этой проекции зависит каждый вес в пакете.
    """
    rng = np.random.default_rng(17)
    n = 12
    equity = np.zeros(n, dtype=bool)
    equity[:5] = True
    gold = np.zeros(n, dtype=bool)
    gold[5:7] = True
    groups = [(equity, 0.35), (gold, 0.10)]
    lo, hi = np.zeros(n), np.full(n, 0.30)

    for _ in range(20):
        v = rng.normal(1 / n, 0.25, n)
        exact = project_capped(v, lo, hi, groups)
        # Дейкстра со щедрым числом витков — эталон, а не соперник.
        slow = _dykstra(v, lo, hi, groups, iterations=400)
        assert exact.sum() == pytest.approx(1.0, abs=1e-9)
        assert exact[equity].sum() <= 0.35 + 1e-9
        assert exact[gold].sum() <= 0.10 + 1e-9
        assert np.abs(exact - slow).max() < 1e-6


def _dykstra(v, lo, hi, groups, *, iterations):
    """Поочерёдные проекции с поправками — независимый эталон для теста."""
    x = v.copy()
    corrections = [np.zeros_like(v) for _ in range(len(groups) + 1)]
    for _ in range(iterations):
        y = x + corrections[0]
        x = project_box_simplex(y, lo, hi)
        corrections[0] = y - x
        for k, (mask, cap) in enumerate(groups, start=1):
            y = x + corrections[k]
            total = float(y[mask].sum())
            z = y.copy()
            if total > cap:
                z[mask] = y[mask] - (total - cap) / float(mask.sum())
            x = z
            corrections[k] = y - x
    return x


def test_сжатие_ковариации_сохраняет_дисперсии() -> None:
    panel = _panel(periods=300, width=5)
    sample = np.cov(panel.values, rowvar=False, bias=True)
    shrunk = shrunk_covariance(panel.values)
    # Диагональ цели совпадает с выборочной — сжатие её не двигает.
    assert np.allclose(np.diag(shrunk), np.diag(sample), rtol=1e-6)
    # И матрица остаётся положительно определённой — иначе оптимизатор
    # найдёт «безрисковый» портфель из воздуха.
    assert float(np.linalg.eigvalsh(shrunk).min()) > -1e-12


def test_оптимум_не_превышает_целевой_волатильности() -> None:
    panel = _panel()
    cov = shrunk_covariance(panel.values) * 252
    mu = panel.values.mean(axis=0) * 252
    c = Constraints(lo=np.zeros(panel.width), hi=np.full(panel.width, 0.34))

    # Недостижимая цель: тише, чем позволяет минимальная дисперсия при этих
    # потолках, не бывает. Возвращается самый спокойный состав, а не отказ.
    quietest = optimise_to_volatility(mu, cov, c, target_volatility=0.0)
    floor = float(np.sqrt(quietest @ cov @ quietest))
    assert quietest.sum() == pytest.approx(1.0, abs=1e-6)

    # Достижимая цель обязана выполняться точно.
    target = floor + 0.02
    w = optimise_to_volatility(mu, cov, c, target_volatility=target)
    vol = float(np.sqrt(w @ cov @ w))
    assert w.sum() == pytest.approx(1.0, abs=1e-6)
    assert vol <= target + 1e-3
    # И при этом целью пользуются: доходность выше, чем у самого тихого.
    assert w @ mu > quietest @ mu


def test_бутстрэп_берёт_блоки_подряд() -> None:
    rows = np.arange(20).reshape(20, 1).astype(float)
    rng = np.random.default_rng(1)
    sample = block_bootstrap(rows, rng, block=5)
    assert sample.shape == rows.shape
    # В выборке обязаны найтись подряд идущие исходные строки — иначе это
    # обычная перестановка, а она рвёт кластеры волатильности.
    flat = sample[:, 0]
    steps = np.diff(flat) % 20
    assert (steps == 1).sum() >= 12


def test_ресэмплинг_даёт_допустимый_состав() -> None:
    panel = _panel(periods=600, width=5)
    groups = (np.array([True, False, False, False, False]), 0.10)
    c = Constraints(
        lo=np.zeros(5), hi=np.full(5, 0.4), groups=(groups,)
    )
    result = resampled_weights(
        panel.values, c, target_volatility=0.10, draws=25
    )
    assert result.draws == 25
    assert result.weights.sum() == pytest.approx(1.0, abs=1e-6)
    assert result.weights[0] <= 0.10 + 1e-6
    # Разброс веса по выборкам — не декорация: он обязан быть измерен.
    assert result.dispersion.shape == (5,)


def test_скользящая_проверка_не_видит_будущего() -> None:
    panel = _panel(periods=700, width=4)
    seen: list[int] = []

    def build(train: np.ndarray) -> np.ndarray:
        seen.append(train.shape[0])
        return np.full(panel.width, 1 / panel.width)

    report = walk_forward(panel, build, train=250, test=60)
    assert report.performed
    assert len(report.folds) >= 3
    # Каждому окну достаётся ровно окно обучения, ни днём больше.
    assert set(seen) == {250}
    for fold in report.folds:
        assert fold.test_to - fold.train_to == 60


def test_короткая_история_не_проверяется_а_отказывает() -> None:
    panel = _panel(periods=100, width=3)
    report = walk_forward(panel, lambda t: np.full(3, 1 / 3), train=250, test=60)
    assert not report.performed
    assert "проверка не проводилась" in report.reason
    verdict = judge(report, drawdown_limit=0.2)
    assert not verdict.admitted


def test_вердикт_отклоняет_состав_с_большой_просадкой() -> None:
    rng = np.random.default_rng(3)
    # Ряд с устойчивым падением: любая просадка тут больше любого лимита.
    values = rng.normal(-0.004, 0.02, size=(700, 2))
    panel = ReturnPanel(
        tuple(date(2020, 1, 1) + timedelta(days=i) for i in range(700)),
        ("A", "B"),
        values,
    )
    report = walk_forward(panel, lambda t: np.array([0.5, 0.5]), train=250, test=60)
    verdict = judge(report, drawdown_limit=0.10)
    assert not verdict.admitted
    assert verdict.reasons


def test_метрики_ряда_считаются_по_определению() -> None:
    # Ряд из двух дней: +10%, затем −10% от нового уровня.
    series = np.log(np.array([1.10, 0.90]))
    result = performance(series, periods_per_year=252)
    assert result.periods == 2
    assert result.max_drawdown == pytest.approx(0.10, abs=1e-9)
