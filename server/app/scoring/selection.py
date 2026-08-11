"""Отбор карточек дня (engine-ТЗ §16).

Три правила, которые здесь важнее кода:

1. **Квота и нормализация внимания независимы по торговым контурам.** FORTS
   и crypto не должны вытеснять или переранжировать друг друга только потому,
   что в одном скане одновременно нашлось много кандидатов. Общий риск между
   ними остаётся общим на этапе исполнения.
2. **Не более одной идеи из кластера при превышении риска кластера.**
   Три лонга по нефти — это одна сделка тройного размера.
3. **«Сделок нет» — валидный результат** (§32). Он оформляется как результат
   с причиной, а не как пустой экран.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
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


def _presentation_lane(instrument_id: str) -> str:
    """Контур, который получает собственную квоту и шкалу ранжирования.

    Это **не** риск-кластер и не новый торговый фильтр. Один общий пул создавал
    две нежелательные связи: FORTS занимал места Bybit, а его экстремальные
    score/EV меняли min/max нормализации и могли переставить местами уже сами
    crypto-идеи. Оба эффекта относятся к presentation, не к стратегии.
    """
    if instrument_id.startswith("CRYPTO:"):
        return "crypto"
    if instrument_id.startswith("MOEX:FUT:"):
        return "forts"
    return "other"


def rank(
    ideas: list[RankedIdea], weights: RankingWeights | None = None
) -> list[RankedResult]:
    """Ранжирование §16.7 с разложением по слагаемым."""
    w = weights or RankingWeights()
    if w.total() != Decimal(1):
        raise ValueError(f"сумма весов ранжирования = {w.total()}, должна быть 1")
    if not ideas:
        return []

    ev_low = min(i.expected_r for i in ideas)
    ev_high = max(i.expected_r for i in ideas)
    score_low = min(i.score for i in ideas)
    score_high = max(i.score for i in ideas)

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


def _rank_for_presentation(
    ideas: list[RankedIdea], weights: RankingWeights | None
) -> list[RankedResult]:
    """Нормализовать каждую площадку только относительно её кандидатов.

    Затем результаты можно слить для одного экрана: квота всё равно считается
    на lane. Абсолютное rank_value между площадками не является сравнением
    «FORTS лучше BTC» — это лишь порядок отрисовки двух независимых списков.
    """
    lanes: dict[str, list[RankedIdea]] = defaultdict(list)
    for idea in ideas:
        lanes[_presentation_lane(idea.instrument_id)].append(idea)

    ranked: list[RankedResult] = []
    for lane_ideas in lanes.values():
        ranked.extend(rank(lane_ideas, weights))
    return sorted(ranked, key=lambda result: result.rank_value, reverse=True)


def select_daily(
    ideas: list[RankedIdea],
    *,
    max_cards: int = 3,
    cluster_risk_exceeded: set[str] | None = None,
    weights: RankingWeights | None = None,
) -> DailySelection:
    """Собрать выдачу дня (§16).

    FORTS и crypto имеют независимые ``max_cards`` и независимую нормализацию.
    Общий cluster/open risk остаётся общим и применяется при исполнении.
    """
    exceeded = cluster_risk_exceeded or set()
    ranked = _rank_for_presentation(ideas, weights)

    taken_clusters: set[str] = set()
    trade_now: list[RankedResult] = []
    waiting_candidates: list[RankedResult] = []
    waiting: list[RankedResult] = []
    dropped: list[RankedResult] = []
    active_per_lane: dict[str, int] = defaultdict(int)

    for result in ranked:
        item = result.idea
        cluster = item.cluster
        lane = _presentation_lane(item.instrument_id)
        if cluster and cluster in exceeded and cluster in taken_clusters:
            dropped.append(
                RankedResult(
                    item,
                    result.rank_value,
                    result.parts,
                    dropped_reason=(
                        f"кластер {cluster} уже представлен, а риск кластера "
                        "превышен — три сделки одного кластера это одна сделка "
                        "тройного размера"
                    ),
                )
            )
            continue

        if (
            item.quality_status is QualityStatus.ACTIVE
            and active_per_lane[lane] < max_cards
        ):
            trade_now.append(result)
            active_per_lane[lane] += 1
            if cluster:
                taken_clusters.add(cluster)
        elif item.quality_status is QualityStatus.WATCH:
            waiting_candidates.append(result)
        elif item.quality_status is QualityStatus.ACTIVE:
            dropped.append(
                RankedResult(
                    item,
                    result.rank_value,
                    result.parts,
                    dropped_reason=f"не поместилась в {max_cards} карточки lane {lane}",
                )
            )
        else:
            dropped.append(
                RankedResult(
                    item,
                    result.rank_value,
                    result.parts,
                    dropped_reason="статус REJECTED",
                )
            )

    waiting_per_lane: dict[str, int] = defaultdict(int)
    for result in waiting_candidates:
        lane = _presentation_lane(result.idea.instrument_id)
        free = max_cards - active_per_lane[lane] - waiting_per_lane[lane]
        if free > 0:
            waiting.append(result)
            waiting_per_lane[lane] += 1
        else:
            dropped.append(
                RankedResult(
                    result.idea,
                    result.rank_value,
                    result.parts,
                    dropped_reason=f"не поместилась в {max_cards} карточки lane {lane}",
                )
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
