"""Инструменты, свечи, признаки и режим рынка (engine-ТЗ §22)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Money, Price, Quantity, Ratio, UuidPk, utcnow_column
from .enums import AssetClass, DerivativesFlow, LiquidityRegime, Timeframe
from .enums import TrendRegime, Venue, VolatilityRegime


class Instrument(UuidPk, Base):
    """Спецификация инструмента (`instrument-master`, §3.2).

    Шаг цены, стоимость шага и лот — не справочная информация, а множители в
    формуле размера позиции (§17.1). Инструмент без них торговать нельзя, и
    это выражено ограничением базы, а не проверкой в коде.
    """

    __tablename__ = "instruments"

    # Каноническая форма «MOEX:FUT:RIU6» / «CRYPTO:PERP:BTCUSDT» (§4.3).
    instrument_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    venue: Mapped[Venue] = mapped_column(String(16), nullable=False)
    asset_class: Mapped[AssetClass] = mapped_column(String(24), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RUB")

    tick_size: Mapped[Price] = mapped_column(nullable=False)
    tick_value: Mapped[Money] = mapped_column(nullable=False)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    quantity_step: Mapped[Quantity] = mapped_column(nullable=False, default=Decimal(1))
    min_quantity: Mapped[Quantity] = mapped_column(nullable=False, default=Decimal(1))
    min_notional: Mapped[Money] = mapped_column(nullable=True)
    contract_multiplier: Mapped[Ratio] = mapped_column(nullable=False, default=Decimal(1))

    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Следующая серия для роллирования (§5.2): без неё фьючерс не проходит фильтр.
    next_contract: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Кластер корреляции (§17.2) — задаётся справочником, уточняется динамикой.
    correlation_cluster: Mapped[str | None] = mapped_column(String(32), nullable=True)

    is_tradable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    in_universe: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    universe_note: Mapped[str] = mapped_column(String(256), nullable=False, default="")

    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utcnow_column()
    updated_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint("tick_size > 0", name="tick_size_positive"),
        CheckConstraint("tick_value > 0", name="tick_value_positive"),
        CheckConstraint("lot_size > 0", name="lot_size_positive"),
        CheckConstraint("quantity_step > 0", name="quantity_step_positive"),
        Index("ix_instruments_universe", "venue", "in_universe"),
    )


class Bar(Base):
    """Каноническая свеча (§4.3).

    ``is_closed`` — не украшение: §4.4 прямо запрещает использовать
    незакрытую свечу как закрытую. Хранить её надо (по ней видно текущую
    реакцию цены), но признак обязан ехать вместе с данными.
    """

    __tablename__ = "bars"

    instrument_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("instruments.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    timeframe: Mapped[Timeframe] = mapped_column(String(8), primary_key=True)
    open_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )

    open: Mapped[Price] = mapped_column(nullable=False)
    high: Mapped[Price] = mapped_column(nullable=False)
    low: Mapped[Price] = mapped_column(nullable=False)
    close: Mapped[Price] = mapped_column(nullable=False)
    volume_units: Mapped[Quantity] = mapped_column(nullable=True)
    volume_notional: Mapped[Money] = mapped_column(nullable=True)
    open_interest: Mapped[Quantity] = mapped_column(nullable=True)

    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), nullable=False, default=list
    )
    ingested_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint("high >= low", name="high_not_below_low"),
        CheckConstraint("high >= open AND high >= close", name="high_is_max"),
        CheckConstraint("low <= open AND low <= close", name="low_is_min"),
        Index("ix_bars_lookup", "instrument_id", "timeframe", "open_time"),
    )


class MarketFeature(Base):
    """Снимок признаков на закрытии бара (`feature-engine`, §3.3).

    Хранится отдельно от свечи, потому что набор признаков версионируется:
    пересчёт с новой версией не должен затирать то, на чём принимались
    прошлые решения (§18 — исходную идею нельзя перезаписывать).
    """

    __tablename__ = "market_features"

    instrument_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("instruments.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    timeframe: Mapped[Timeframe] = mapped_column(String(8), primary_key=True)
    bar_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    feature_version: Mapped[str] = mapped_column(String(32), primary_key=True)

    atr: Mapped[Price] = mapped_column(nullable=True)
    ema_fast: Mapped[Price] = mapped_column(nullable=True)
    ema_slow: Mapped[Price] = mapped_column(nullable=True)
    adx: Mapped[Ratio] = mapped_column(nullable=True)
    rsi: Mapped[Ratio] = mapped_column(nullable=True)
    volume_z: Mapped[Ratio] = mapped_column(nullable=True)
    relative_volume: Mapped[Ratio] = mapped_column(nullable=True)
    oi_change_z: Mapped[Ratio] = mapped_column(nullable=True)
    funding_percentile: Mapped[Ratio] = mapped_column(nullable=True)
    realized_volatility: Mapped[Ratio] = mapped_column(nullable=True)
    atr_percentile: Mapped[Ratio] = mapped_column(nullable=True)

    # Свинги, уровни, FVG, order blocks — структура целиком, чтобы детекторы
    # не пересчитывали её на каждый запрос карточки.
    structure_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    computed_at: Mapped[datetime] = utcnow_column()


class RegimeSnapshot(Base):
    """Классификация режима (§9).

    Четыре независимых измерения. Их нельзя схлопнуть в одно число: «тренд
    вверх при экстремальной волатильности и тонкой ликвидности» — это не
    «хороший рынок с оговорками», а запрет на вход.
    """

    __tablename__ = "regime_snapshots"

    instrument_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("instruments.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    timeframe: Mapped[Timeframe] = mapped_column(String(8), primary_key=True)
    bar_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)

    trend: Mapped[TrendRegime] = mapped_column(String(16), nullable=False)
    trend_score: Mapped[int] = mapped_column(Integer, nullable=False)
    volatility: Mapped[VolatilityRegime] = mapped_column(String(16), nullable=False)
    liquidity: Mapped[LiquidityRegime] = mapped_column(String(16), nullable=False)
    derivatives_flow: Mapped[DerivativesFlow] = mapped_column(String(20), nullable=False)

    detail_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    computed_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint(
            "trend_score BETWEEN -5 AND 5", name="trend_score_in_range"
        ),
    )


class DataQualityEvent(Base):
    """Отказ или деградация источника.

    UX-ТЗ §12 требует хранить «ошибку данных: source outage, stale, gap,
    correction». Без такой таблицы просадка, вызванная дырой в данных,
    неотличима в журнале от просадки, вызванной стратегией.
    """

    __tablename__ = "data_quality_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    occurred_at: Mapped[datetime] = utcnow_column()
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    instrument_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    flag: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_dq_events_time", "occurred_at"),)
