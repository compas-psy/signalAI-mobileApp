"""Торговые идеи, их неизменяемая история и исходы (engine-ТЗ §18, §22)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..strategy_identity import (
    LEGACY_CONTROL_CONFIG_HASH,
    LEGACY_CONTROL_GENERATED_STAGE,
    LEGACY_CONTROL_ROLE,
    LEGACY_CONTROL_SOURCE_SHA,
    LEGACY_CONTROL_VERSION,
    LEGACY_RISK_POLICY_VERSION,
)
from .base import (
    Base,
    Money,
    Price,
    Quantity,
    Ratio,
    StrEnumColumn,
    UuidPk,
    Versioned,
    utcnow_column,
)
from .enums import (
    BarrierOutcome,
    Direction,
    IdeaStatus,
    OrderIntent,
    QualityStatus,
    SkipReason,
    Strategy,
)


def _strategy_family_default(context) -> str:
    """Copy the already-selected family into provenance without gating runtime."""

    value = context.get_current_parameters().get("strategy")
    return value.value if isinstance(value, Strategy) else str(value)


class TradeIdea(UuidPk, Versioned, Base):
    """Идея со всем, что нужно, чтобы её исполнить и потом оспорить.

    Поля плана (вход, стоп, цели, размер) заполняются в момент выпуска и
    **не переписываются**: §18 — «Исходную идею нельзя перезаписывать».
    Всё, что меняется дальше, живёт в :class:`IdeaEvent`.

    Вероятность хранится с явным определением (§7): это P(TP1 раньше SL в
    пределах горизонта), а не «шанс, что сделка хорошая». ``confidence``
    отдельным полем, потому что уверенность в оценке и сама оценка — разные
    вещи (§15.4), и смешивать их значит обманывать себя.
    """

    __tablename__ = "trade_ideas"

    instrument_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("instruments.instrument_id"), nullable=False
    )
    strategy: Mapped[Strategy] = mapped_column(StrEnumColumn(Strategy, 24), nullable=False)

    # ── Стратегическая provenance ───────────────────────────────────────
    # Эти поля описывают, какой именно код/конфиг создал идею. Они НЕ
    # участвуют в eligibility, admission или execution и потому не могут
    # выключить legacy path. Для текущего runtime defaults идентичны
    # legacy_control_v1; будущие candidates обязаны передавать свои значения
    # явно, не меняя baseline defaults.
    strategy_family: Mapped[str] = mapped_column(
        String(32), nullable=False, default=_strategy_family_default
    )
    strategy_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default=LEGACY_CONTROL_VERSION
    )
    strategy_role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=LEGACY_CONTROL_ROLE
    )
    strategy_config_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default=LEGACY_CONTROL_CONFIG_HASH
    )
    strategy_code_ref: Mapped[str] = mapped_column(
        String(64), nullable=False, default=LEGACY_CONTROL_SOURCE_SHA
    )
    risk_policy_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default=LEGACY_RISK_POLICY_VERSION
    )
    generated_stage: Mapped[str] = mapped_column(
        String(24), nullable=False, default=LEGACY_CONTROL_GENERATED_STAGE
    )

    direction: Mapped[Direction] = mapped_column(StrEnumColumn(Direction, 8), nullable=False)
    status: Mapped[IdeaStatus] = mapped_column(StrEnumColumn(IdeaStatus, 24), nullable=False)
    quality_status: Mapped[QualityStatus] = mapped_column(StrEnumColumn(QualityStatus, 12), nullable=False)

    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    context_timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    setup_timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    trigger_timeframe: Mapped[str] = mapped_column(String(8), nullable=False)

    # ── План (§22, ключевые поля trade_ideas) ────────────────────────────
    order_intent: Mapped[OrderIntent] = mapped_column(StrEnumColumn(OrderIntent, 24), nullable=False)
    entry_low: Mapped[Price] = mapped_column(nullable=False)
    entry_high: Mapped[Price] = mapped_column(nullable=False)
    entry_reference: Mapped[Price] = mapped_column(nullable=False)
    stop: Mapped[Price] = mapped_column(nullable=False)
    tp1: Mapped[Price] = mapped_column(nullable=False)
    tp2: Mapped[Price] = mapped_column(nullable=False)
    tp3: Mapped[Price] = mapped_column(nullable=True)
    invalidation: Mapped[str] = mapped_column(Text, nullable=False, default="")

    rr_tp1: Mapped[Ratio] = mapped_column(nullable=False)
    rr_tp2: Mapped[Ratio] = mapped_column(nullable=False)

    # ── Оценка и вероятность (§15) ───────────────────────────────────────
    score: Mapped[Ratio] = mapped_column(nullable=False)
    data_quality: Mapped[Ratio] = mapped_column(nullable=False)
    p_tp1_before_sl: Mapped[Ratio] = mapped_column(nullable=False)
    p_tp2_before_sl: Mapped[Ratio] = mapped_column(nullable=True)
    p_positive_r_after_costs: Mapped[Ratio] = mapped_column(nullable=True)
    expected_r: Mapped[Ratio] = mapped_column(nullable=False)
    confidence: Mapped[Ratio] = mapped_column(nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    probability_source: Mapped[str] = mapped_column(String(48), nullable=False)

    # ── Риск и размер (§17.1) ────────────────────────────────────────────
    risk_pct: Mapped[Ratio] = mapped_column(nullable=False)
    risk_amount: Mapped[Money] = mapped_column(nullable=False)
    quantity: Mapped[Quantity] = mapped_column(nullable=False)
    risk_per_unit: Mapped[Money] = mapped_column(nullable=False)
    correlation_cluster: Mapped[str | None] = mapped_column(String(32), nullable=True)
    drawdown_multiplier: Mapped[Ratio] = mapped_column(nullable=False)

    # ── Объяснение (§24) ─────────────────────────────────────────────────
    # Ровно тот JSON, что уходит в приложение. LLM его пересказывает, но не
    # меняет ни одного числа (§0.4, §24).
    explanation_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # ── Доказательства и разметка (§9.1, §10.6) ──────────────────────────
    # Хранятся в снимке идеи, а не пересчитываются при показе: пересчёт на
    # свежих барах нарисовал бы не ту разметку, по которой принималось
    # решение, и разбор сделки перестал бы соответствовать сделке.
    evidence_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    annotations_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    data_warnings: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), nullable=False, default=list
    )

    signal_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    # Показана ли владельцу. §12 UX-ТЗ требует хранить и непоказанные идеи,
    # иначе журнал перестаёт отвечать на вопрос «что мы пропустили».
    was_presented: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    presentation_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    events: Mapped[list["IdeaEvent"]] = relationship(
        back_populates="idea", order_by="IdeaEvent.sequence", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("entry_high >= entry_low", name="entry_band_ordered"),
        CheckConstraint("expires_at > signal_time", name="expiry_after_signal"),
        CheckConstraint("quantity >= 0", name="quantity_not_negative"),
        CheckConstraint(
            "p_tp1_before_sl >= 0 AND p_tp1_before_sl <= 1", name="p_tp1_is_probability"
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="confidence_is_ratio"
        ),
        CheckConstraint("score >= 0 AND score <= 100", name="score_in_range"),
        CheckConstraint(
            "data_quality > 0 AND data_quality <= 1", name="data_quality_is_ratio"
        ),
        # Стоп обязан быть по правильную сторону от входа: длинная позиция со
        # стопом выше входа — не «странный план», а перевёрнутый знак риска.
        CheckConstraint(
            "(direction = 'LONG' AND stop < entry_low)"
            " OR (direction = 'SHORT' AND stop > entry_high)",
            name="stop_on_correct_side",
        ),
        CheckConstraint(
            "(direction = 'LONG' AND tp1 > entry_high AND tp2 > tp1)"
            " OR (direction = 'SHORT' AND tp1 < entry_low AND tp2 < tp1)",
            name="targets_ordered_by_direction",
        ),
        Index("ix_ideas_status", "status", "signal_time"),
        Index("ix_ideas_instrument", "instrument_id", "signal_time"),
    )


class IdeaEvent(Base):
    """Неизменяемое событие жизненного цикла (§18).

    Таблица только дополняется. Обновление и удаление запрещены триггером
    базы (см. миграцию), а не соглашением в коде: журнал, который можно
    переписать, не журнал.

    ``sequence`` растёт внутри идеи и уникален — это защищает от гонки, когда
    два воркера пишут переход одновременно.
    """

    __tablename__ = "idea_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    idea_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("trade_ideas.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = utcnow_column()

    old_status: Mapped[IdeaStatus | None] = mapped_column(StrEnumColumn(IdeaStatus, 24), nullable=True)
    new_status: Mapped[IdeaStatus] = mapped_column(StrEnumColumn(IdeaStatus, 24), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(48), nullable=False)
    reason_detail: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Снимки на момент перехода: без них «почему движок так решил» через месяц
    # не восстановить.
    market_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    feature_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    probability_before: Mapped[Ratio] = mapped_column(nullable=True)
    probability_after: Mapped[Ratio] = mapped_column(nullable=True)

    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_action: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    idea: Mapped[TradeIdea] = relationship(back_populates="events")

    __table_args__ = (
        UniqueConstraint("idea_id", "sequence", name="uq_idea_events_sequence"),
        Index("ix_idea_events_time", "occurred_at"),
    )


class IdeaOutcome(Base):
    """Исход идеи по тройному барьеру (§15.2) плюс фактический результат.

    Три числа рядом намеренно: модельный исход по правилам стратегии,
    идеальное исполнение и то, что получилось на самом деле. Разница между
    ними — это цена исполнения и цена решений владельца, и она должна быть
    видна отдельно (UX-ТЗ §12).
    """

    __tablename__ = "idea_outcomes"

    idea_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("trade_ideas.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    barrier_outcome: Mapped[BarrierOutcome] = mapped_column(StrEnumColumn(BarrierOutcome, 12), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    bars_to_resolution: Mapped[int | None] = mapped_column(Integer, nullable=True)

    model_r: Mapped[Ratio] = mapped_column(nullable=True)
    ideal_r: Mapped[Ratio] = mapped_column(nullable=True)
    actual_r: Mapped[Ratio] = mapped_column(nullable=True)
    actual_pnl: Mapped[Money] = mapped_column(nullable=True)

    mae_r: Mapped[Ratio] = mapped_column(nullable=True)
    mfe_r: Mapped[Ratio] = mapped_column(nullable=True)
    entry_slippage: Mapped[Price] = mapped_column(nullable=True)
    exit_slippage: Mapped[Price] = mapped_column(nullable=True)
    fees: Mapped[Money] = mapped_column(nullable=True)
    funding: Mapped[Money] = mapped_column(nullable=True)

    # Неоднозначный бар (§15.2): TP и SL внутри одной свечи без младшего TF.
    # Такой исход нельзя использовать как чистую метку для обучения.
    label_usable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    detail_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    computed_at: Mapped[datetime] = utcnow_column()


class IdeaSkip(Base):
    """Отказ владельца от показанной идеи с причиной (UX-ТЗ §12, приложение A)."""

    __tablename__ = "idea_skips"

    idea_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("trade_ideas.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    reason: Mapped[SkipReason] = mapped_column(StrEnumColumn(SkipReason, 24), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    skipped_at: Mapped[datetime] = utcnow_column()
    # Снимок оценки на момент отказа: спустя месяц идея выглядит иначе.
    snapshot_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
