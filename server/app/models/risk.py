"""Риск, аудит и реестр моделей (§17, §20, §22)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Money, Ratio, StrEnumColumn, UuidPk, utcnow_column
from .enums import ExecutionMode


class RiskSnapshot(Base):
    """Состояние риск-бюджета на момент времени (§17).

    Снимок пишется перед каждым решением о размере и сохраняется в событии
    идеи. Иначе через месяц невозможно ответить, почему объём был урезан:
    сработала просадка, кластер или дневной лимит.
    """

    __tablename__ = "risk_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    taken_at: Mapped[datetime] = utcnow_column()

    risk_equity: Mapped[Money] = mapped_column(nullable=False)
    open_risk: Mapped[Ratio] = mapped_column(nullable=False, default=0)
    day_pnl_pct: Mapped[Ratio] = mapped_column(nullable=False, default=0)
    week_pnl_pct: Mapped[Ratio] = mapped_column(nullable=False, default=0)
    month_pnl_pct: Mapped[Ratio] = mapped_column(nullable=False, default=0)
    current_drawdown: Mapped[Ratio] = mapped_column(nullable=False, default=0)
    drawdown_multiplier: Mapped[Ratio] = mapped_column(nullable=False, default=1)

    # Какое ограничение сейчас связывает: day | week | month | open | cluster |
    # drawdown | none. Одно слово вместо пяти булевых флагов — на экране
    # владелец должен видеть причину, а не набор галочек.
    binding_limit: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    entries_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    halted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    cluster_risk_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    detail_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_risk_snapshots_time", "taken_at"),)


class RiskState(Base):
    """Текущий режим торговли и kill switch (§21).

    Одна строка. Хранится в базе, а не в памяти процесса: перезапуск сервера
    не должен снимать аварийную остановку. ``kill_switch`` остаётся совместимым
    boolean для старых health/mobile consumers; точное действие хранится в
    ``kill_switch_level`` и разбирается enum-ом в execution/API слое.
    """

    __tablename__ = "risk_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    execution_mode: Mapped[ExecutionMode] = mapped_column(
        StrEnumColumn(ExecutionMode, 20), nullable=False, default=ExecutionMode.PAPER
    )
    kill_switch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    kill_switch_level: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="CLEAR",
        server_default=text("'CLEAR'"),
    )
    kill_switch_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    halted_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)


class AuditEvent(Base):
    """Append-only аудит (§23 UX-ТЗ, §21 engine-ТЗ).

    Сюда пишутся изменения лимитов, включение режимов, срабатывание kill
    switch и любые действия владельца, влияющие на деньги. Токены и полные
    ответы брокера не логируются — только факт и его причина.
    """

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    occurred_at: Mapped[datetime] = utcnow_column()
    actor: Mapped[str] = mapped_column(String(32), nullable=False)  # owner | system
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    subject: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    before_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    after_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("ix_audit_events_time", "occurred_at"),)


class BacktestRun(UuidPk, Base):
    """Прогон бэктеста и его отчёт (§19, §22)."""

    __tablename__ = "backtest_runs"

    label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    strategy: Mapped[str | None] = mapped_column(String(24), nullable=True)
    period_from: Mapped[date] = mapped_column(Date, nullable=False)
    period_to: Mapped[date] = mapped_column(Date, nullable=False)

    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False)
    universe_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    net_return: Mapped[Ratio] = mapped_column(nullable=True)
    profit_factor: Mapped[Ratio] = mapped_column(nullable=True)
    expectancy_r: Mapped[Ratio] = mapped_column(nullable=True)
    max_drawdown: Mapped[Ratio] = mapped_column(nullable=True)
    sharpe: Mapped[Ratio] = mapped_column(nullable=True)
    sortino: Mapped[Ratio] = mapped_column(nullable=True)
    calmar: Mapped[Ratio] = mapped_column(nullable=True)
    brier_score: Mapped[Ratio] = mapped_column(nullable=True)
    # Вероятность переобучения (§19, Bailey et al.) — без неё «хороший
    # бэктест» ничего не значит.
    pbo: Mapped[Ratio] = mapped_column(nullable=True)
    top5_contribution: Mapped[Ratio] = mapped_column(nullable=True)

    report_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    gate_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    gate_detail_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        CheckConstraint("period_to >= period_from", name="period_ordered"),
    )


class ModelRegistry(UuidPk, Base):
    """Champion/challenger реестр моделей вероятности (§20)."""

    __tablename__ = "model_registry"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="challenger")
    algorithm: Mapped[str] = mapped_column(String(48), nullable=False)
    trained_from: Mapped[date] = mapped_column(Date, nullable=False)
    trained_to: Mapped[date] = mapped_column(Date, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    oos_brier: Mapped[Ratio] = mapped_column(nullable=True)
    oos_ece: Mapped[Ratio] = mapped_column(nullable=True)
    calibration_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    feature_list: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    approved_by_human: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    promoted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_model_registry_version"),
        CheckConstraint(
            "role IN ('champion','challenger','retired')", name="role_known"
        ),
    )
