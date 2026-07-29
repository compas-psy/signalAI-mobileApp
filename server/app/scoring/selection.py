"""Отбор карточек дня (engine-ТЗ §16).

Три правила, которые здесь важнее кода:

1. **Не более трёх карточек.** Не «три лучших плюс ещё немного» — ровно три
   места, и за них конкурируют.
2. **Не более одной идеи из кластера при превышении риска кластера.**
   Три лонга по нефти — это одна сделка тройного размера.
3. **«Сделок нет» — валидный результат** (§32). Он оформляется как результат
   с причиной, а не как пустой экран.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..models.enums import LiquidityRegime, QualityStatus


@dataclass(frozen=True, slots=True)
class RankingWeights:
    """Веса §16.7."""

    expected_value: Decimal = Decimal("0.35")
    confidence_adjusted_p: Decimal = Decimal("0.25")
    score: Decimal = Decimal("0.15")
    liquidity: Decimal = Decimal("0.10")
    diversification: Decimal = Decimal("0.10")
    execution: Decimal = Decimal("0.05")

    def total(self) -> Decimal:
        return (
            self.expected_value + self.confidence_adjusted_p + self.score
            + self.liquidity + self.diversification + self.execution
        )


LIQUIDITY_SCORE: dict[LiquidityRegime, Decimal] = {
    LiquidityRegime.GOOD: Decimal(1),
    LiquidityRegime.NORMAL: Decimal("0.6"),
    LiquidityRegime.THIN: Decimal("0.2"),
    LiquidityRegime.UNTRADEABLE: Decimal(0),
}


@dataclass(frozen=True, slots=True)
class RankedIdea:
    """Идея-кандидат в выдачу дня."""

    idea_id: str
    instrument_id: str
    cluster: str | None
    quality_status: QualityStatus
    expected_r: Decimal
    probability: Decimal
    confidence: Decimal
    score: Decimal
    liquidity: LiquidityRegime
    execution_quality: Decimal = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class RankedResult:
    idea: RankedIdea
    rank_value: Decimal
    parts: dict[str, Decimal]
    dropped_reason: str = ""


@dataclass(frozen=True, slots=True)
class DailySelection:
    trade_now: tuple[RankedResult, ...]
    wait_for_trigger: tuple[RankedResult, ...]
    dropped: tuple[RankedResult, ...]
    no_trade_reason: str = ""
    considered: int = 0


def _normalise(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    if high <= low:
        return Decimal("0.5")
    return max(Decimal(0), min(Decimal(1), (value - low) / (high - low)))


def rank(
    ideas: list[RankedIdea], weights: RankingWeights | None = None
) -> list[RankedResult]:
    """Ранжирование §16.7 с разложением по слагаемым.

    Слагаемые сохраняются: «эта идея выше той» должно объясняться, иначе
    порядок карточек выглядит произволом.
    """
    w = weights or RankingWeights()
    if w.total() != Decimal(1):
        raise ValueError(f"сумма весов ранжирования = {w.total()}, должна быть 1")
    if not ideas:
        return []

    ev_low = min(i.expected_r for i in ideas)
    ev_high = max(i.expected_r for i in ideas)
    score_low = min(i.score for i in ideas)
    score_high = max(i.score for i in ideas)

    # Разнообразие: идея из кластера, который уже представлен, ценится ниже.
    seen: dict[str, int] = {}
    for item in ideas:
        key = item.cluster or item.instrument_id
        seen[key] = seen.get(key, 0) + 1

    results: list[RankedResult] = []
    for item in ideas:
        key = item.cluster or item.instrument_id
        diversification = Decimal(1) / Decimal(seen[key])
        parts = {
            "expected_value": w.expected_value * _normalise(item.expected_r, ev_low, ev_high),
            "confidence_adjusted_p": w.confidence_adjusted_p * item.probability * item.confidence,
            "score": w.score * _normalise(item.score, score_low, score_high),
            "liquidity": w.liquidity * LIQUIDITY_SCORE.get(item.liquidity, Decimal(0)),
            "diversification": w.diversification * diversification,
            "execution": w.execution * item.execution_quality,
        }
        results.append(
            RankedResult(
                idea=item,
                rank_value=sum(parts.values()).quantize(Decimal("0.000001")),
                parts=parts,
            )
        )
    return sorted(results, key=lambda r: r.rank_value, reverse=True)


def select_daily(
    ideas: list[RankedIdea],
    *,
    max_cards: int = 3,
    cluster_risk_exceeded: set[str] | None = None,
    weights: RankingWeights | None = None,
) -> DailySelection:
    """Собрать выдачу дня (§16).

    Порядок: сначала готовые к исполнению, затем наблюдение — до общего числа
    ``max_cards``. Наблюдение не вытесняет готовую сделку, но и не оставляет
    экран пустым, когда готовых нет.
    """
    exceeded = cluster_risk_exceeded or set()
    ranked = rank(ideas, weights)

    taken_clusters: set[str] = set()
    trade_now: list[RankedResult] = []
    waiting: list[RankedResult] = []
    dropped: list[RankedResult] = []

    for result in ranked:
        item = result.idea
        cluster = item.cluster
        if cluster and cluster in exceeded and cluster in taken_clusters:
            dropped.append(
                RankedResult(
                    item, result.rank_value, result.parts,
                    dropped_reason=(
                        f"кластер {cluster} уже представлен, а риск кластера "
                        "превышен — три сделки одного кластера это одна сделка "
                        "тройного размера"
                    ),
                )
            )
            continue

        if item.quality_status is QualityStatus.ACTIVE and len(trade_now) < max_cards:
            trade_now.append(result)
            if cluster:
                taken_clusters.add(cluster)
        elif item.quality_status is QualityStatus.WATCH:
            waiting.append(result)
        else:
            dropped.append(
                RankedResult(
                    item, result.rank_value, result.parts,
                    dropped_reason="статус REJECTED",
                )
            )

    free = max(0, max_cards - len(trade_now))
    overflow = waiting[free:]
    waiting = waiting[:free]
    dropped.extend(
        RankedResult(r.idea, r.rank_value, r.parts,
                     dropped_reason=f"не поместилась в {max_cards} карточки дня")
        for r in overflow
    )

    reason = ""
    if not trade_now and not waiting:
        reason = (
            "готовых сделок нет и ждать нечего. Это валидный результат: "
            "приложение не создаёт сделки ради нормы (§0.7, §32)."
        )
    elif not trade_now:
        reason = (
            "готовых к исполнению сделок нет — показаны кандидаты, "
            "ожидающие триггера"
        )

    return DailySelection(
        trade_now=tuple(trade_now),
        wait_for_trigger=tuple(waiting),
        dropped=tuple(dropped),
        no_trade_reason=reason,
        considered=len(ideas),
    )
