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
    annualise_mean,
    block_bootstrap,
    build_panel,
    optimise_to_volatility,
    performance,
    project_box_simplex,
    project_capped,
    project_with_groups,
    resampled_weights,
    shrunk_covariance,
    shrunk_mean,
)
from app.portfolio.build import _drop_unstable
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


def test_нулевой_состав_не_считается_портфелем() -> None:
    """Вектор из нулей — это «состав не собрался», а не портфель без дохода.

    Ровно так и выглядело на боевом сервере: потолки консервативного профиля
    не давали набрать 100% из отобранных бумаг, проекция возвращала нули,
    скользящая проверка честно мерила ряд из нулей и сообщала «вне обучения
    состав теряет 0.0% годовых, прибыльных окон 0%». Владелец читал это как
    приговор рынку, хотя речь о невыполнимых ограничениях.
    """
    panel = _panel(periods=700, width=4)
    report = walk_forward(panel, lambda t: np.zeros(4), train=250, test=60)
    assert not report.performed
    assert "ограничения профиля" in report.reason
    assert not judge(report, drawdown_limit=0.2).admitted


def test_осмысленная_проверка_требует_нескольких_окон() -> None:
    """Три окна — девять месяцев вне обучения: оценка обязана назвать себя грубой."""
    panel = _panel(periods=250 + 3 * 60, width=3)
    report = walk_forward(
        panel, lambda t: np.full(3, 1 / 3), train=250, test=60
    )
    assert report.performed
    assert len(report.folds) == 3
    verdict = judge(report, drawdown_limit=0.9)
    assert any("грубая" in w for w in verdict.warnings)


# ── Сжатие ожидаемой доходности (Jorion, Bayes–Stein) ────────────────────


def _mu_cov(returns):
    from app.portfolio.stats import annualise_cov, shrunk_covariance

    return annualise_cov(shrunk_covariance(returns))


def test_сжатие_тянет_средние_к_портфелю_минимальной_дисперсии():
    """Выборочное среднее — худший вход оптимизации, и его надо сжимать.

    Ковариацию мы сжимали с самого начала, а среднее брали выборочным. Это
    не мелочь, а несимметричность в самом чувствительном месте.
    """
    rng = np.random.default_rng(3)
    returns = np.column_stack([
        rng.normal(0.0002, 0.004, 500),
        rng.normal(0.0006, 0.012, 500),
        rng.normal(-0.0001, 0.010, 500),
    ])
    sample = annualise_mean(returns)
    shrunk = shrunk_mean(returns, _mu_cov(returns))

    # Разброс сжатых оценок строго меньше выборочного: в этом весь смысл.
    assert shrunk.std() < sample.std()
    # И сжатие не выносит оценки за пределы исходного разброса.
    assert shrunk.min() >= sample.min() - 1e-9
    assert shrunk.max() <= sample.max() + 1e-9


def test_сжатие_сохраняет_порядок_бумаг():
    """Выпуклая комбинация с общей точкой: кто был выше — выше и остался.

    Это принципиально. Если бы сжатие меняло порядок, оно меняло бы и
    решение оптимизатора — то есть было бы не стабилизацией оценки, а
    подменой рыночной картины.
    """
    rng = np.random.default_rng(4)
    returns = np.column_stack([
        rng.normal(0.0008, 0.006, 400),
        rng.normal(0.0003, 0.006, 400),
        rng.normal(-0.0004, 0.006, 400),
    ])
    sample = annualise_mean(returns)
    shrunk = shrunk_mean(returns, _mu_cov(returns))
    assert list(np.argsort(sample)) == list(np.argsort(shrunk))


def test_короткое_окно_сжимается_сильнее_длинного():
    """Чем меньше наблюдений, тем меньше веры выборочному среднему."""
    rng = np.random.default_rng(6)
    long_returns = np.column_stack([
        rng.normal(0.0005, 0.008, 1000),
        rng.normal(0.0001, 0.008, 1000),
        rng.normal(0.0003, 0.008, 1000),
    ])
    short_returns = long_returns[:120]

    def pull(returns):
        sample = annualise_mean(returns)
        shrunk = shrunk_mean(returns, _mu_cov(returns))
        # Доля пути, пройденного к общей точке.
        return float(np.mean(np.abs(shrunk - sample)) / max(sample.std(), 1e-12))

    assert pull(short_returns) > pull(long_returns)


def test_сжатие_не_ломается_на_вырожденной_ковариации():
    """Две одинаковые бумаги — матрица вырождена, а ответ нужен всё равно."""
    rng = np.random.default_rng(8)
    column = rng.normal(0.0004, 0.007, 300)
    returns = np.column_stack([column, column])
    result = shrunk_mean(returns, _mu_cov(returns))
    assert np.all(np.isfinite(result))
    assert result.size == 2


def test_на_одном_активе_сжимать_нечего():
    rng = np.random.default_rng(10)
    returns = rng.normal(0.0004, 0.007, (300, 1))
    sample = annualise_mean(returns)
    shrunk = shrunk_mean(returns, _mu_cov(returns))
    assert shrunk == pytest.approx(sample, abs=1e-6)


def test_сжатие_среднего_не_меняет_состав_при_целевой_волатильности():
    """Свойство, которое нельзя не знать: под целевой волатильностью сжатие
    среднего — математический ноль.

    Веса дают в сумме единицу, поэтому общая добавка к среднему из задачи
    выпадает; остаётся умножение среднего на (1 − k), а это то же самое, что
    деление неприятия риска на (1 − k). Целевая волатильность подбирает
    неприятие риска заново — и приводит ровно в ту же точку границы.

    Тест стоит здесь, чтобы никто не «улучшил» устойчивость, добавив сжатие
    ещё раз и посчитав задачу решённой.
    """
    rng = np.random.default_rng(2026)
    returns = rng.normal(0.0003, 0.010, (504, 8))
    cov = _mu_cov(returns)
    constraints = Constraints(lo=np.zeros(8), hi=np.full(8, 0.30), groups=())

    sample = optimise_to_volatility(
        annualise_mean(returns), cov, constraints, target_volatility=0.12
    )
    shrunk = optimise_to_volatility(
        shrunk_mean(returns, cov), cov, constraints, target_volatility=0.12
    )
    assert np.abs(sample - shrunk).sum() == pytest.approx(0.0, abs=1e-6)


def test_позиция_неотличимая_от_шума_из_состава_убирается():
    """Разброс веса больше самого веса — это не доля, а «мы не знаем».

    Ресэмплинг усредняет сотню составов, и такая бумага в среднем выглядит
    прилично: 6% в одной выборке, ноль в другой, 12% в третьей.
    """
    weights = np.array([0.5, 0.3, 0.2])
    dispersion = np.array([0.05, 0.02, 0.4])
    result = _drop_unstable(weights, dispersion)
    assert result[2] == 0.0
    assert result[0] == 0.5 and result[1] == 0.3


def test_если_устойчивых_позиций_не_осталось_состав_не_обнуляется():
    """«Не уверены ни в чём» — повод сказать это на проверке, а не молча
    отдать пустой портфель."""
    weights = np.array([0.5, 0.5])
    dispersion = np.array([0.9, 0.9])
    assert np.array_equal(_drop_unstable(weights, dispersion), weights)
