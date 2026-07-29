"""Схемы торговых идей (engine-ТЗ §22, §24).

Карточка идеи обязана нести определение вероятности вместе с самой
вероятностью. §32: «probability имеет строгое определение; confidence показан
отдельно». Число 0,61 без подписи «TP1 раньше SL в течение 7 дней» — это
приглашение прочитать его как «шанс заработать», а это другое число.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field

from ..models.enums import (
    Direction,
    IdeaStatus,
    OrderIntent,
    QualityStatus,
    SkipReason,
    Strategy,
)
from .common import ApiModel, Money


class TimeframeNote(ApiModel):
    tf: str
    role: str
    summary: str


class ProbabilityBlock(ApiModel):
    """Вероятность вместе со всем, без чего её нельзя читать (§7, §15.3)."""

    definition: str = Field(
        default="P(TP1 достигнут раньше SL в пределах горизонта, "
        "с учётом модели исполнения)"
    )
    p_tp1_before_sl: Money
    p_tp2_before_sl: Money | None = None
    p_positive_r_after_costs: Money | None = None
    expected_r: Money
    confidence: Money
    confidence_band: str
    sample_size: int
    source: str
    # §15.3: до 100 релевантных OOS-сделок вероятность ограничена сверху.
    capped: bool = False
    cap_reason: str = ""


class PlanBlock(ApiModel):
    """План сделки. Всё, чем он исполняется, и ничего сверх того."""

    order_intent: OrderIntent
    entry_low: Money
    entry_high: Money
    entry_reference: Money
    stop: Money
    tp1: Money
    tp2: Money
    tp3: Money | None = None
    rr_tp1: Money
    rr_tp2: Money
    invalidation: str


class SizingBlock(ApiModel):
    """Размер позиции с разложением, а не одним числом (§17.1).

    Владелец должен видеть, что именно связало объём: базовый риск, множитель
    просадки или упёршийся лимит. Иначе урезанный размер выглядит как ошибка.
    """

    risk_pct: Money
    risk_amount: Money
    quantity: Money
    risk_per_unit: Money
    drawdown_multiplier: Money
    binding_limit: str
    correlation_cluster: str | None = None
    # §20.1: если объём меньше лота, идея остаётся информационной.
    tradable: bool = True
    not_tradable_reason: str = ""


class ScoreComponent(ApiModel):
    name: str
    label: str
    weight: Money
    value: Money
    points: Money
    detail: str = ""
    # Отличает «измерено и плохо» от «не измерено вовсе» — без этого
    # отсутствующий фактор читается как отрицательный.
    measured: bool = True


class ScoreBlock(ApiModel):
    total: Money
    data_quality: Money
    components: list[ScoreComponent]


class ExplanationBlock(ApiModel):
    """Контракт объяснения (§24). LLM пересказывает, но не меняет числа."""

    headline: str
    thesis: str
    market_regime: dict[str, str]
    timeframes: list[TimeframeNote] = Field(default_factory=list)
    supporting_factors: list[str] = Field(default_factory=list)
    counter_factors: list[str] = Field(default_factory=list)
    invalidation: str = ""
    data_warnings: list[str] = Field(default_factory=list)


class IdeaSummary(ApiModel):
    """Карточка в ленте (§25 Ideas)."""

    id: UUID
    instrument_id: str
    symbol: str = ""
    strategy: Strategy
    direction: Direction
    status: IdeaStatus
    quality_status: QualityStatus
    horizon_days: int
    score: Money
    p_tp1_before_sl: Money
    confidence: Money
    expected_r: Money
    rr_tp2: Money
    risk_amount: Money
    signal_time: datetime
    expires_at: datetime


class EvidenceOut(ApiModel):
    """Доказательство, на которое ссылаются оценка и разметка (§9.1).

    ``conflicts_with`` не украшение: ТЗ требует **показывать** конфликт
    детекторов, а не сглаживать его. Поле существовало в модели приложения
    с самого начала и никогда не заполнялось — панель конфликтов была пуста,
    чем бы ни противоречили друг другу показания.
    """

    id: str
    kind: str
    title: str
    detail: str = ""
    summary: str = ""
    confidence: float = 0.0
    detector_version: str = ""
    measured: bool = True
    missing_terms: list[str] = Field(default_factory=list)
    measures: list[dict] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)


class AnnotationOut(ApiModel):
    """Метка на графике (§10.6).

    Имена полей — snake_case, как во всём остальном API. А вот **значение**
    ``type`` остаётся из словаря §10.6 (``smcOrderBlock``, ``wyckoffSpring``):
    это не имя поля, а термин интерфейса, и переименовывать его вслед за
    внутренними именами движка значит связать несвязанное.
    """

    id: str
    type: str
    timeframe: str
    start_time: datetime
    end_time: datetime
    price_low: float | None = None
    price_high: float | None = None
    confidence: float = 1.0
    evidence_id: str
    detector_version: str = ""
    label: str = ""
    display_priority: int = 50


class IdeaDetail(IdeaSummary):
    """Полная карточка (§25 Idea detail)."""

    context_timeframe: str
    setup_timeframe: str
    trigger_timeframe: str
    plan: PlanBlock
    probability: ProbabilityBlock
    sizing: SizingBlock
    score_breakdown: ScoreBlock
    explanation: ExplanationBlock
    # §9.1: тезис, оценка и график ссылаются на одни и те же объекты.
    evidence: list[EvidenceOut] = Field(default_factory=list)
    annotations: list[AnnotationOut] = Field(default_factory=list)
    config_hash: str
    engine_version: str
    feature_version: str
    was_presented: bool


class IdeaEventOut(ApiModel):
    sequence: int
    occurred_at: datetime
    old_status: IdeaStatus | None
    new_status: IdeaStatus
    reason_code: str
    reason_detail: str
    probability_before: Money | None = None
    probability_after: Money | None = None
    user_action: bool
    engine_version: str
    config_hash: str


class DailyCards(ApiModel):
    """Выдача дня (§16).

    Три списка, а не один: «нет сделки» — валидный результат (§32), и он
    обязан выглядеть как результат, а не как пустой экран.
    """

    generated_at: datetime
    trade_now: list[IdeaSummary] = Field(default_factory=list)
    wait_for_trigger: list[IdeaSummary] = Field(default_factory=list)
    no_trade_reason: str = ""
    scanned_instruments: int = 0
    rejected_count: int = 0


class SkipRequest(ApiModel):
    reason: SkipReason
    comment: str = ""


class RejectRequest(ApiModel):
    reason: str
    comment: str = ""
