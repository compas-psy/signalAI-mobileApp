"""Теневой портфель (ТЗ §19.9).

Портфель без денег. Нужен ровно для одного: посмотреть, что получилось бы,
если бы по подтверждённым гипотезам действительно входили, — и посмотреть
честно, с издержками, с невозможными входами и с периодами, когда входить
было некуда.

Три правила, каждое против своего способа приукрасить результат.

**Одна позиция на причинный кластер.** Пять гипотез об одной истории — это
одна ставка, сделанная пять раз. Портфель, который берёт их все, показывает
диверсификацию, которой нет, и в плохом сценарии теряет впятеро.

**Без плеча и с равным риском.** Оптимизация весов под исторический
результат превращает проверку в подгонку: веса, подобранные по прошлому,
объясняют его идеально.

**Невозможный вход записывается.** Сигнал, по которому нельзя было войти,
не исчезает из статистики — иначе покрытие стратегии окажется выше
реального, а вместе с ним и результат.

Периоды без позиций сохраняются отдельно. Портфель, который «в среднем
зарабатывает», но полгода стоял в деньгах, — это другой портфель, чем тот,
что работал всё время.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .backtest import Costs, EventResult

# Сколько держим позицию, если гипотеза не закрылась раньше (§19.4).
DEFAULT_HORIZON_DAYS = 365

# Максимум одновременных позиций. Не оптимизация, а ограничение
# сопоставимости: портфель из двух бумаг и портфель из двадцати — разные
# объекты, и сравнивать их результаты нельзя.
MAX_POSITIONS = 20


@dataclass(frozen=True, slots=True)
class Position:
    """Одна позиция теневого портфеля."""

    hypothesis_id: str
    cluster_id: str
    opened_at: datetime
    closed_at: datetime | None
    weight: Decimal
    excess_return: Decimal
    reason_closed: str = ""


@dataclass
class ShadowReport:
    positions: list[Position] = field(default_factory=list)
    skipped_same_cluster: list[str] = field(default_factory=list)
    skipped_not_executable: list[str] = field(default_factory=list)
    cash_days: int = 0
    total_days: int = 0
    excess_return: Decimal = Decimal(0)
    max_drawdown: Decimal = Decimal(0)

    @property
    def coverage(self) -> Decimal:
        """Доля времени, когда портфель был в позициях.

        «В среднем зарабатывает» у портфеля, полгода простоявшего в
        деньгах, означает не то же самое, что у работавшего всё время.
        """
        if self.total_days <= 0:
            return Decimal(0)
        return _q(
            Decimal(self.total_days - self.cash_days) / Decimal(self.total_days)
        )


def _q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def build(
    results: list[EventResult],
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    max_positions: int = MAX_POSITIONS,
) -> ShadowReport:
    """Собрать теневой портфель из результатов проверки.

    Порядок обработки — по времени входа: портфель не может знать, что
    через месяц появится более интересная гипотеза, и выбирать из будущего
    ему нечем.
    """
    report = ShadowReport()

    ordered = sorted(
        (r for r in results if r.entry_time is not None),
        key=lambda r: r.entry_time,  # type: ignore[arg-type,return-value]
    )
    for result in results:
        if not result.executable or result.entry_time is None:
            report.skipped_not_executable.append(result.hypothesis_id)

    open_clusters: dict[str, datetime] = {}
    for result in ordered:
        entry = result.entry_time
        assert entry is not None
        # Освобождаем кластеры, чьи позиции уже закрылись к этому моменту.
        expired = [
            cluster
            for cluster, closes in open_clusters.items()
            if closes <= entry
        ]
        for cluster in expired:
            del open_clusters[cluster]

        if result.cluster_id in open_clusters:
            # Пять гипотез об одной истории — одна ставка, сделанная пять
            # раз. Диверсификации в этом нет.
            report.skipped_same_cluster.append(result.hypothesis_id)
            continue
        if len(open_clusters) >= max_positions:
            report.skipped_same_cluster.append(result.hypothesis_id)
            continue

        closes = entry + timedelta(days=horizon_days)
        open_clusters[result.cluster_id] = closes
        report.positions.append(
            Position(
                hypothesis_id=result.hypothesis_id,
                cluster_id=result.cluster_id,
                opened_at=entry,
                closed_at=closes,
                # Равный вес: оптимизация под исторический результат
                # превращает проверку в подгонку.
                weight=Decimal(1),
                excess_return=result.excess.get(horizon_days, Decimal(0)),
                reason_closed="horizon",
            )
        )

    if report.positions:
        first = min(p.opened_at for p in report.positions)
        last = max(p.closed_at or p.opened_at for p in report.positions)
        report.total_days = max(1, (last - first).days)

        # Дни без единой открытой позиции.
        busy: set[int] = set()
        for position in report.positions:
            end = position.closed_at or position.opened_at
            start_day = (position.opened_at - first).days
            end_day = (end - first).days
            busy.update(range(start_day, end_day + 1))
        report.cash_days = max(0, report.total_days - len(busy))

        weights = Decimal(len(report.positions))
        report.excess_return = _q(
            sum((p.excess_return for p in report.positions), Decimal(0)) / weights
        )

        # Просадка по последовательности результатов, а не по сумме: важно
        # не только «сколько в итоге», но и «сколько было в худшей точке».
        running = Decimal(0)
        peak = Decimal(0)
        worst = Decimal(0)
        for position in sorted(report.positions, key=lambda p: p.opened_at):
            running += position.excess_return
            peak = max(peak, running)
            worst = min(worst, running - peak)
        report.max_drawdown = _q(worst)

    return report


__all__ = [
    "DEFAULT_HORIZON_DAYS",
    "MAX_POSITIONS",
    "Position",
    "ShadowReport",
    "build",
]
