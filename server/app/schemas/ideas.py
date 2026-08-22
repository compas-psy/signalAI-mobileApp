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
    """План сделки. Всё, чем он исполняется, и ничего сверх того.

    `tp_shares` — такой же подписываемый параметр, как цены целей. Клиент не
    имеет права придумывать 40/40/20 сам: иначе server-paper и экран снова
    разойдутся при первой смене management policy.
    """

    order_intent: OrderIntent
    entry_low: Money
    entry_high: Money
    entry_reference: Money
    stop: Money
    tp1: Money
    tp2: Money
    tp3: Money | None = None
    tp_shares: list[Money] = Field(default_factory=list)
    rr_tp1: Money
    rr_tp2: Money
    invalidation: str


class SizingBlock(ApiModel):
    """Размер позиции с разложением, а не одним числом (§17.1).

    Владелец должен видеть, что именно связало объём: базовый риск, множитель
    просадки или упёршийся лимит. Иначе урезанный размер выглядит как ошибка.
    """

    risk_pct: Money
    # В рублях: бюджет задан в них, и убыток по стопу считается по этому числу.
    risk_amount: Money
    # В номинале инструмента — 0,348 ETH или 3 контракта, а не «штуки».
    quantity: Money
    # В валюте котировки: у ETHUSDT это USDT, у фьючерса MOEX — рубли.
    risk_per_unit: Money
    drawdown_multiplier: Money
    binding_limit: str
    correlation_cluster: str | None = None

    # Чем меряется объём. Без шага «0,348 ETH» негде округлить, а без имени
    # единицы число читается как штуки — и 0,348 штуки выглядит поломкой.
    quantity_step: Money = Decimal(1)
    quantity_unit: str = ""
    # Валюта котировки и курс, по которому рубли получены. Курс — тот, что
    # участвовал в расчёте, а не сегодняшний: карточка живёт днями.
    quote_currency: str = "RUB"
    quote_rate_rub: Money | None = None
    quote_note: str = ""

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


class EconomicEventCalendarBlock(ApiModel):
    """Server-owned event-calendar result shown with the immutable idea."""

    status: str = "UNAVAILABLE"
    reason_code: str = "EVENT_SOURCE_UNAVAILABLE"
    detail: str = "календарь событий недоступен"
    blocks_admission: bool = True
    event: dict | None = None


class EvidenceMeasureOut(ApiModel):
    """Одно измерение внутри доказательства детектора.

    Это ровно тот JSON, который сохраняет ``pipeline.presentation``. Он
    намеренно остаётся рядом с Evidence: measured/not_applicable/missing —
    разные факты, и API не вправе потерять эту разницу при сериализации.
    """

    name: str
    label: str
    value: float | None = None
    status: str = "measured"
    note: str = ""


class EvidenceOut(ApiModel):
    """Доказательство в фактически подписанном формате §9.1.

    ``TradeIdea.evidence_json`` заполняется ``build_evidence`` и с самого
    начала хранит ``kind/title/detail/confidence/detector_version``. Мобильный
    клиент читает тот же контракт. Предыдущая API-схема ошибочно требовала
    другой, нигде не создаваемый набор ``detector/timeframe/role/...`` и
    поэтому ``GET /ideas/{id}`` падал на каждой живой идее. Summary оставался
    на экране без TradePlan — отсюда пустые Entry/SL/TP и пропавшая кнопка
    подтверждения у уже TRIGGERED+ACTIVE идей.
    """

    id: str
    kind: str
    title: str = ""
    detail: str = ""
    summary: str = ""
    confidence: float
    detector_version: str = ""
    measured: bool = True
    missing_terms: list[str] = Field(default_factory=list)
    measures: list[EvidenceMeasureOut] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)
    # Связь с графиком может быть обогащена API поверх сохранённого evidence.
    annotation_ids: list[str] = Field(default_factory=list)


class AnnotationOut(ApiModel):
    id: str
    type: str
    timeframe: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    price_low: Money | None = None
    price_high: Money | None = None
    confidence: Money
    evidence_id: str
    detector_version: str
    label: str
    display_priority: int = 50


class IdeaSummary(ApiModel):
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


class IdeaDetail(IdeaSummary):
    context_timeframe: str
    setup_timeframe: str
    trigger_timeframe: str
    plan: PlanBlock
    probability: ProbabilityBlock
    sizing: SizingBlock
    evidence: list[EvidenceOut]
    annotations: list[AnnotationOut]
    score_breakdown: ScoreBlock
    explanation: ExplanationBlock
    event_calendar: EconomicEventCalendarBlock = Field(
        default_factory=EconomicEventCalendarBlock
    )
    config_hash: str
    engine_version: str
    feature_version: str
    was_presented: bool
    closing_reason: str = ""


class DailyCards(ApiModel):
    generated_at: datetime
    trade_now: list[IdeaSummary]
    wait_for_trigger: list[IdeaSummary]
    no_trade_reason: str = ""


class IdeaEventOut(ApiModel):
    sequence: int
    old_status: IdeaStatus | None
    new_status: IdeaStatus
    reason_code: str
    reason_detail: str
    occurred_at: datetime
    market_snapshot: dict = Field(default_factory=dict)


class SkipRequest(ApiModel):
    reason: SkipReason
    comment: str = ""
