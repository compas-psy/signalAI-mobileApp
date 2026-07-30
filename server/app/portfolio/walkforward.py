"""Проверка состава на истории, которой он не видел (§19).

Портфель, посчитанный на всей истории и на ней же измеренный, всегда выглядит
отлично — это свойство арифметики, а не состава. Единственная проверка,
которая что-то значит, — скользящая: веса считаются по окну обучения и
применяются к следующему отрезку, ни один день которого в подборе не
участвовал. Отрезки идут внахлёст по всей доступной истории, и метрики
считаются по склейке всех «будущих» кусков.

Отдельно про отказ. Если истории не хватает на одно полное окно, проверка не
«проходит с оговоркой» — она не проводится, и пакет получает статус «не
проверен». Разница принципиальная: непроверенный состав нельзя показывать
рядом с проверенным, как будто это одно и то же.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from .stats import Performance, ReturnPanel, performance

# Веса считаются по окну обучения; сигнатура намеренно узкая — строитель
# получает только то, что имеет право видеть.
Builder = Callable[[np.ndarray], np.ndarray]


@dataclass(frozen=True, slots=True)
class Fold:
    train_from: int
    train_to: int
    test_to: int
    result: Performance
    turnover: float


@dataclass(frozen=True, slots=True)
class WalkForward:
    """Итог скользящей проверки."""

    performed: bool
    reason: str
    folds: tuple[Fold, ...] = ()
    combined: Performance | None = None
    mean_turnover: float = 0.0
    worst_fold_drawdown: float = 0.0
    positive_folds: int = 0

    @property
    def summary(self) -> str:
        if not self.performed:
            return self.reason
        c = self.combined
        assert c is not None
        return (
            f"окон {len(self.folds)}, вне обучения: доходность {c.cagr:.1%}, "
            f"колебания {c.volatility:.1%}, просадка {c.max_drawdown:.1%}, "
            f"прибыльных окон {self.positive_folds} из {len(self.folds)}"
        )


def walk_forward(
    panel: ReturnPanel,
    build: Builder,
    *,
    train: int,
    test: int,
    step: int | None = None,
    periods_per_year: int = 252,
) -> WalkForward:
    """Прогнать скользящую проверку по панели доходностей."""
    T = panel.periods
    stride = step or test
    if T < train + test:
        return WalkForward(
            performed=False,
            reason=(
                f"истории {T} дней, для одного окна нужно {train + test} — "
                "проверка не проводилась"
            ),
        )

    folds: list[Fold] = []
    out_of_sample: list[np.ndarray] = []
    previous: np.ndarray | None = None
    turnovers: list[float] = []

    start = 0
    while start + train + test <= T:
        train_slice = panel.values[start : start + train]
        test_slice = panel.values[start + train : start + train + test]
        weights = build(train_slice)
        if weights.size != panel.width or not np.isfinite(weights).all():
            start += stride
            continue
        series = test_slice @ weights
        out_of_sample.append(series)
        turnover = 0.0 if previous is None else float(np.abs(weights - previous).sum())
        turnovers.append(turnover)
        previous = weights
        folds.append(
            Fold(
                train_from=start,
                train_to=start + train,
                test_to=start + train + test,
                result=performance(series, periods_per_year=periods_per_year),
                turnover=turnover,
            )
        )
        start += stride

    if not folds:
        return WalkForward(
            performed=False,
            reason="ни одно окно не дало состава — проверка не проводилась",
        )

    combined = performance(np.concatenate(out_of_sample), periods_per_year=periods_per_year)
    return WalkForward(
        performed=True,
        reason="",
        folds=tuple(folds),
        combined=combined,
        mean_turnover=float(np.mean(turnovers)) if turnovers else 0.0,
        worst_fold_drawdown=max(f.result.max_drawdown for f in folds),
        positive_folds=sum(1 for f in folds if f.result.cagr > 0),
    )


@dataclass(frozen=True, slots=True)
class Verdict:
    """Допуск состава по итогам проверки (§19.2)."""

    admitted: bool
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default=())

    @property
    def note(self) -> str:
        if self.admitted and not self.warnings:
            return "проверка на истории пройдена"
        parts = [*self.reasons, *self.warnings]
        head = "проверка пройдена" if self.admitted else "не допущен"
        return f"{head}: " + "; ".join(parts)


def judge(
    report: WalkForward,
    *,
    drawdown_limit: float,
    min_positive_share: float = 0.5,
) -> Verdict:
    """Пропускать ли состав владельцу.

    Пороги простые и проверяемые: вне обучения состав не должен терять
    больше заявленного лимита просадки и должен зарабатывать чаще, чем
    подбрасывание монеты. Всё, что не дотянуло, называет причину — пакет
    исчезает с экрана не молча.
    """
    if not report.performed:
        return Verdict(False, (report.reason,))
    combined = report.combined
    assert combined is not None

    reasons: list[str] = []
    warnings: list[str] = []
    if combined.max_drawdown > drawdown_limit:
        reasons.append(
            f"просадка вне обучения {combined.max_drawdown:.1%} "
            f"выше допустимой {drawdown_limit:.1%}"
        )
    share = report.positive_folds / max(1, len(report.folds))
    if share < min_positive_share:
        reasons.append(
            f"прибыльных окон {share:.0%} — меньше половины"
        )
    if combined.cagr <= 0:
        reasons.append(f"доходность вне обучения {combined.cagr:.1%}")
    if report.mean_turnover > 0.6:
        warnings.append(
            f"состав меняется на {report.mean_turnover:.0%} за пересчёт — "
            "издержки ребаланса будут заметны"
        )
    if len(report.folds) < 4:
        warnings.append(f"окон всего {len(report.folds)} — оценка грубая")
    return Verdict(not reasons, tuple(reasons), tuple(warnings))
