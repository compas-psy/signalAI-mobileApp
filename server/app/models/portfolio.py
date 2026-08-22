"""Портфель: позиции, модельные веса, черновики ребаланса (§6, §22)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import (
    Base,
    Money,
    Price,
    Quantity,
    Ratio,
    StrEnumColumn,
    UuidPk,
    utcnow_column,
)
from .enums import AssetClass, PackageSize, RiskProfile


class Account(UuidPk, Base):
    """Счёт у брокера или на бирже.

    Инвестиционный и риск-контуры независимы (§2): риск-счёт не использует
    инвестиционный капитал как обеспечение. Разделение выражено полем
    ``circuit``, и риск-движок считает лимиты только по риск-контуру.
    """

    __tablename__ = "accounts"

    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    broker: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RUB")
    circuit: Mapped[str] = mapped_column(String(16), nullable=False)  # investment | risk
    equity: Mapped[Money] = mapped_column(nullable=False, default=0)
    free_margin: Mapped[Money] = mapped_column(nullable=True)
    synced_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        UniqueConstraint("broker", "external_id", name="uq_accounts_broker"),
        CheckConstraint("circuit IN ('investment','risk')", name="circuit_known"),
    )


class Holding(Base):
    """Фактическая позиция на дату.

    История хранится, а не перезаписывается: ребаланс сравнивает «было» и
    «стало», и без снимков это сравнение не восстановить.
    """

    __tablename__ = "holdings"

    account_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    instrument_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, primary_key=True)

    quantity: Mapped[Quantity] = mapped_column(nullable=False)
    average_price: Mapped[Price] = mapped_column(nullable=True)
    market_price: Mapped[Price] = mapped_column(nullable=True)
    market_value: Mapped[Money] = mapped_column(nullable=False, default=0)
    asset_class: Mapped[AssetClass] = mapped_column(StrEnumColumn(AssetClass, 24), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="broker")


class PortfolioModel(UuidPk, Base):
    """Модельный портфель: профиль × размер пакета (§6.1, §6.5).

    ``valid_until`` обязателен: UX-ТЗ §7.1 — «Пакет протухает и требует
    пересчёта». Показывать вчерашнюю модель как сегодняшнюю рекомендацию
    нельзя.
    """

    __tablename__ = "portfolio_models"

    profile: Mapped[RiskProfile] = mapped_column(StrEnumColumn(RiskProfile, 16), nullable=False)
    package: Mapped[PackageSize] = mapped_column(StrEnumColumn(PackageSize, 16), nullable=False)
    horizon_years: Mapped[int] = mapped_column(Integer, nullable=False)

    expected_return_low: Mapped[Ratio] = mapped_column(nullable=False)
    expected_return_high: Mapped[Ratio] = mapped_column(nullable=False)
    target_volatility: Mapped[Ratio] = mapped_column(nullable=False)
    model_drawdown_limit: Mapped[Ratio] = mapped_column(nullable=False)
    cvar_95: Mapped[Ratio] = mapped_column(nullable=True)

    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Состав годен, но рискованнее, чем обещает профиль. Прятать такой нельзя:
    # на рынке, пережившем 2022 год, «не уложился в целевую просадку» — это
    # свойство рынка, а не брак состава. Владелец видит числа и решает сам.
    meets_target: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    warnings_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    stress_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    generated_at: Mapped[datetime] = utcnow_column()
    valid_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    weights: Mapped[list["PortfolioWeight"]] = relationship(
        back_populates="model", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint(
            "expected_return_high >= expected_return_low", name="return_band_ordered"
        ),
        CheckConstraint("valid_until > generated_at", name="model_expires_later"),
        Index("ix_portfolio_models_lookup", "profile", "package", "generated_at"),
    )


class PortfolioWeight(Base):
    """Целевой вес инструмента в модели.

    Сумма весов проверяется кодом при сохранении (UX-ТЗ §7.1: «Сумма ровно
    100%»), а потолок крипты — отдельным ограничением (§6.4, §27).

    ``evidence_json`` хранит точную декомпозицию screening score и гипотезы,
    которые были decision-available при сборке модели.
    """

    __tablename__ = "portfolio_weights"

    model_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("portfolio_models.id", ondelete="CASCADE"),
        primary_key=True,
    )
    instrument_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    asset_class: Mapped[AssetClass] = mapped_column(StrEnumColumn(AssetClass, 24), nullable=False)
    target_weight: Mapped[Ratio] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    thesis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kill_conditions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    score: Mapped[Ratio] = mapped_column(nullable=True)
    expected_return: Mapped[Ratio] = mapped_column(nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    model: Mapped[PortfolioModel] = relationship(back_populates="weights")

    __table_args__ = (
        CheckConstraint(
            "target_weight >= 0 AND target_weight <= 1", name="weight_is_ratio"
        ),
    )


class RebalanceDraft(UuidPk, Base):
    """Черновик ребаланса (§6.6, UX-ТЗ §7.2).

    Именно черновик: MVP заявок не отправляет. Хранится целиком, потому что
    журнал должен помнить и то, что было предложено, и то, что владелец с
    этим сделал.
    """

    __tablename__ = "rebalance_drafts"

    model_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("portfolio_models.id", ondelete="RESTRICT"),
        nullable=False,
    )
    trigger_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    actions_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    before_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    after_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # ``None`` is semantically different from zero: it means fee inputs were
    # unavailable, so this is not an economics-actionable manual proposal.
    estimated_costs: Mapped[Money | None] = mapped_column(nullable=True)
    estimated_tax: Mapped[Money | None] = mapped_column(nullable=True)
    # Kept for legacy readers.  ``economics_status`` distinguishes UNKNOWN
    # from ESTIMATED; a boolean alone cannot make that safety distinction.
    tax_is_estimate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    economics_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="UNKNOWN"
    )
    economics_provenance_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    # Broker settlement is never written over the owner-facing estimate.
    broker_final_costs: Mapped[Money | None] = mapped_column(nullable=True)
    broker_final_tax: Mapped[Money | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint(
            "economics_status IN ('UNKNOWN','ESTIMATED','BROKER_FINAL')",
            name="economics_status_known",
        ),
        CheckConstraint(
            "estimated_costs IS NULL OR estimated_costs >= 0",
            name="estimated_costs_non_negative",
        ),
        CheckConstraint(
            "estimated_tax IS NULL OR estimated_tax >= 0",
            name="estimated_tax_non_negative",
        ),
        CheckConstraint(
            "broker_final_costs IS NULL OR broker_final_costs >= 0",
            name="broker_final_costs_non_negative",
        ),
        CheckConstraint(
            "broker_final_tax IS NULL OR broker_final_tax >= 0",
            name="broker_final_tax_non_negative",
        ),
    )


class PortfolioRun(UuidPk, Base):
    """Отчёт одного прогона сборки пакетов (§6).

    Нужен затем, что «состава нет» — это два разных ответа. Либо конвейер до
    оптимизации не дошёл (мало данных, короткая общая история), либо составы
    посчитаны и ни один не прошёл проверку на истории. На экране оба
    выглядели одинаково — точкой «нет», — и владелец не мог понять, ждать ему
    данных или менять требования.

    Хранится последний прогон, а не история: это состояние конвейера, и
    вопрос к нему всегда один — «что сейчас мешает».
    """

    __tablename__ = "portfolio_runs"

    started_at: Mapped[datetime] = utcnow_column()
    universe: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    screened: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    common_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    built: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    admitted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Почему прогон не дал состава — словами, как их увидит владелец.
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Причины отказа по каждому варианту пакета: профиль × размер × горизонт.
    reasons_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Что не взято в пересчёт и почему: бумаги без истории, без оборота.
    dropped_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (
        Index("ix_portfolio_runs_started", "started_at"),
    )
