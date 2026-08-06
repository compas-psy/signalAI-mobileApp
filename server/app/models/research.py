"""Исследовательский контур ранних сигналов (ТЗ Early Signals §5, §8, §12, §16).

Отдельный контур, а не расширение идей, и это не вопрос удобства раскладки.
Идея — это план сделки: зона входа, стоп, цели, срок жизни в днях, исполнение
по биометрии. Гипотеза — это утверждение об экономике эмитента на горизонте
года-двух, которое проверяется наблюдениями и опровергается фактами. У них
разные горизонты, разные критерии истинности и разные последствия ошибки,
поэтому они не смешиваются ни в базе, ни на экране.

ТЗ прямо запрещает превращать гипотезу в команду (§1.2, §16.4): ни BUY, ни
SELL, ни размера позиции в выдаче быть не может. Модель это выражает
структурно — здесь нет ни цены входа, ни стопа, ни объёма, и добавить их
означало бы сломать замысел, а не расширить его.

Три вещи, которые модель обязана хранить и без которых результат
недоказуем:

* **Две временные оси** (§2.2). Когда событие случилось, когда стало
  публичным, когда мы его увидели и с какого момента им вообще можно
  пользоваться в проверке на истории. Без ``tradable_at`` бэктест видит
  будущее.
* **Происхождение** (§2.4). Пересказ пресс-релиза тремя агентствами — это
  один источник, а не три. Независимость считается по ``lineage_root_id``,
  а не по домену.
* **Разделение факта, расчёта и вывода** (§2.3). Наблюдение, признак,
  сигнал и гипотеза — разные таблицы, и текст модели никогда не становится
  наблюдением.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Ratio, StrEnumColumn, Timestamp, UuidPk, utcnow_column
from .enums import (
    EvidenceRole,
    FactLabel,
    HypothesisState,
    LicenseStatus,
    ResearchDirection,
    SignalStatus,
)

# JSONB на Postgres, JSON везде — тесты на sqlite не должны требовать
# отдельной ветки в коде.
Json = JSON().with_variant(JSONB(), "postgresql")


class ResearchSource(Base):
    """Источник данных и его правовой режим (§5.1).

    Запись существует **до** адаптера и без неё сбор не запускается. Это не
    бюрократия: ТЗ §2.1 требует fail-closed, то есть неизвестный правовой
    статус запрещает сетевой вызов так же, как прямой запрет. Источник,
    у которого истёк срок проверки условий, тоже перестаёт быть разрешённым
    сам, без чьего-либо решения.
    """

    __tablename__ = "research_sources"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    owner: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    base_url: Mapped[str] = mapped_column(String(400), nullable=False, default="")

    # api / file / rss / html / manual / rpc
    access_method: Mapped[str] = mapped_column(String(16), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(24), nullable=False, default="none")
    license_status: Mapped[LicenseStatus] = mapped_column(
        StrEnumColumn(LicenseStatus, 24), nullable=False
    )
    usage_scope: Mapped[str] = mapped_column(String(64), nullable=False, default="internal_research")

    # fetch / store_raw / transform / display_derived / redistribute_raw
    allowed_operations: Mapped[dict] = mapped_column(Json, nullable=False, default=dict)

    terms_url: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    terms_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_review_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    expected_frequency: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    max_staleness: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    source_timezone: Mapped[str] = mapped_column(String(48), nullable=False, default="UTC")
    rate_limit_policy: Mapped[dict] = mapped_column(Json, nullable=False, default=dict)

    # Почему источник в таком статусе. Пустая строка у approved допустима,
    # у всех остальных — нет: заблокированный источник без причины
    # невозможно ни разблокировать, ни объяснить.
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    created_at: Mapped[datetime] = utcnow_column()


class CollectionPermit(UuidPk, Base):
    """Разрешение на один сбор, выданное шлюзом (§5.3).

    Пишется и при отказе. Журнал, в котором есть только успехи, не отвечает
    на главный вопрос аудита — пытались ли обойти запрет.
    """

    __tablename__ = "research_permits"

    # Без внешнего ключа намеренно. Попытка собрать из источника, которого
    # нет в реестре, — самая ценная запись аудита: именно так выглядит
    # обход правового шлюза. Ссылочная целостность стёрла бы её.
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    requested_operations: Mapped[list] = mapped_column(Json, nullable=False, default=list)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    issued_at: Mapped[datetime] = utcnow_column()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_research_permits_source", "source_id", "issued_at"),
    )


class ResearchObservation(UuidPk, Base):
    """Публичный факт с полной цепочкой происхождения (§8.2).

    ``value_numeric`` и ``value_text`` заполняются по отдельности: факт —
    это либо число с единицей измерения, либо утверждение с цитатой. Запись,
    у которой есть и то и другое, обычно означает, что модель пересказала
    число словами, а это уже вывод, а не наблюдение.
    """

    __tablename__ = "research_observations"

    #: Машинный ключ ряда, а не подпись. Человекочитаемое название
    #: показателя живёт в `source_locator`, где его никто не ограничивает;
    #: сюда оно попадало напрямую, и первое же длинное название роняло весь
    #: шаг разведки на `value too long for character varying(48)` — унося с
    #: собой всё, что шаг успел собрать. Код собирается в `research.codes`
    #: так, чтобы влезать по построению; ширина здесь — запас, не защита.
    observation_type: Mapped[str] = mapped_column(String(96), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("research_sources.source_id"), nullable=False
    )

    # ── Экономическое время ──────────────────────────────────────────────
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)

    # ── Время доступности ────────────────────────────────────────────────
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[Timestamp] = mapped_column(nullable=False)
    ingested_at: Mapped[datetime] = utcnow_column()
    # Первая точка, с которой факт разрешён в проверке на истории.
    # Считается детерминированно, см. research.timeline.
    tradable_at: Mapped[Timestamp] = mapped_column(nullable=False)
    publication_time_uncertain: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # ── Происхождение ────────────────────────────────────────────────────
    # Общий корень пересказов. Три новости об одном релизе несут один
    # lineage_root_id и в правиле 3–2–1 считаются одним источником.
    lineage_root_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_locator: Mapped[dict] = mapped_column(Json, nullable=False, default=dict)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    value_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    scale: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    fact_label: Mapped[FactLabel] = mapped_column(
        StrEnumColumn(FactLabel, 24), nullable=False, default=FactLabel.FACT
    )
    quality_flags: Mapped[list] = mapped_column(Json, nullable=False, default=list)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    # Данные разработки: fixtures разрешены, но обязаны называть себя (§26.2).
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        Index("ix_research_obs_entity", "entity_id", "tradable_at"),
        Index("ix_research_obs_type", "observation_type", "tradable_at"),
        # Migration 0008 stored the already-prefixed constraint name under
        # the active naming convention. PostgreSQL truncated that persisted
        # identifier with the deterministic ``c95d`` suffix. Keep metadata
        # aligned instead of scheduling a no-op drop/recreate on every check.
        CheckConstraint(
            "scale > 0", name="ck_research_observations_scale_c95d"
        ),
    )


class ResearchSignal(UuidPk, Base):
    """Срабатывание одного движка (§12.1).

    Четыре числа вместо одного, и это требование §2.6: сила экономического
    изменения, надёжность доказательств, релевантность эмитенту и штраф за
    риск ложной причины измеряются отдельно. Один сводный балл без
    расшифровки запрещён прямо.
    """

    __tablename__ = "research_signals"

    strategy_key: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    detected_at: Mapped[datetime] = utcnow_column()
    as_of: Mapped[Timestamp] = mapped_column(nullable=False)

    direction: Mapped[ResearchDirection] = mapped_column(
        StrEnumColumn(ResearchDirection, 16), nullable=False
    )
    strength: Mapped[Ratio] = mapped_column(nullable=False)
    signed_strength: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    evidence_confidence: Mapped[Ratio] = mapped_column(nullable=False)
    economic_relevance: Mapped[Ratio] = mapped_column(nullable=False)
    freshness: Mapped[Ratio] = mapped_column(nullable=False)
    risk_penalty: Mapped[Ratio] = mapped_column(nullable=False, default=Decimal(0))

    status: Mapped[SignalStatus] = mapped_column(
        StrEnumColumn(SignalStatus, 24), nullable=False, default=SignalStatus.ACTIVE
    )
    components: Mapped[list] = mapped_column(Json, nullable=False, default=list)
    reason_codes: Mapped[list] = mapped_column(Json, nullable=False, default=list)
    evidence_ids: Mapped[list] = mapped_column(Json, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    config_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    __table_args__ = (
        Index("ix_research_signals_lookup", "strategy_key", "entity_id", "as_of"),
    )


class ResearchHypothesis(UuidPk, Base):
    """Причинное объединение сигналов (§16.1).

    Самостоятельный объект аудита: по нему должно быть понятно, что
    утверждается, чем это доказано, что его опровергнет и когда оно
    протухнет — без разговора с моделью.
    """

    __tablename__ = "research_hypotheses"

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Отпечаток замысла (§14.5): сущность, направление, семейство KPI,
    # драйвер и корзина лага. Новое доказательство поднимает версию той же
    # гипотезы, а не плодит одинаковые.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    market: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    direction: Mapped[ResearchDirection] = mapped_column(
        StrEnumColumn(ResearchDirection, 16), nullable=False
    )
    state: Mapped[HypothesisState] = mapped_column(
        StrEnumColumn(HypothesisState, 24), nullable=False
    )

    as_of: Mapped[Timestamp] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = utcnow_column()

    causal_path_json: Mapped[dict] = mapped_column(Json, nullable=False, default=dict)
    target_kpis_json: Mapped[list] = mapped_column(Json, nullable=False, default=list)
    expected_lag: Mapped[str] = mapped_column(String(16), nullable=False, default="")

    evidence_score: Mapped[Ratio] = mapped_column(nullable=False, default=Decimal(0))
    economic_score: Mapped[Ratio] = mapped_column(nullable=False, default=Decimal(0))
    # null означает «не измерено» и никогда не подменяется средним (§12.4).
    market_context_score: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    market_context_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown"
    )
    research_priority: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=Decimal(0)
    )

    three_two_one_json: Mapped[dict] = mapped_column(Json, nullable=False, default=dict)
    investability_json: Mapped[dict] = mapped_column(Json, nullable=False, default=dict)
    fact_summary_json: Mapped[list] = mapped_column(Json, nullable=False, default=list)
    alternative_explanations_json: Mapped[list] = mapped_column(Json, nullable=False, default=list)
    risk_flags: Mapped[list] = mapped_column(Json, nullable=False, default=list)
    falsifiers_json: Mapped[list] = mapped_column(Json, nullable=False, default=list)
    missing_evidence_json: Mapped[list] = mapped_column(Json, nullable=False, default=list)

    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    config_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    engine_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    __table_args__ = (
        UniqueConstraint("fingerprint", "version", name="uq_research_hypotheses_fingerprint"),
        Index("ix_research_hypotheses_state", "state", "research_priority"),
    )


class HypothesisEvidence(UuidPk, Base):
    """Доказательство при гипотезе (§8.2).

    Опровергающее доказательство не удаляется никогда: доля противоречий
    участвует в решении (§14.3), а гипотеза, из которой вычистили неудобные
    факты, перестаёт быть проверяемой.
    """

    __tablename__ = "research_hypothesis_evidence"

    hypothesis_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey(
            "research_hypotheses.id",
            ondelete="CASCADE",
            name="fk_hypothesis_evidence_hypothesis",
        ),
        nullable=False,
    )
    observation_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    signal_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    role: Mapped[EvidenceRole] = mapped_column(
        StrEnumColumn(EvidenceRole, 16), nullable=False
    )
    lineage_root_id: Mapped[str] = mapped_column(String(64), nullable=False)
    independence_group: Mapped[str] = mapped_column(String(48), nullable=False, default="")
    claim_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_locator: Mapped[dict] = mapped_column(Json, nullable=False, default=dict)
    confidence: Mapped[Ratio] = mapped_column(nullable=False, default=Decimal(1))
    weight: Mapped[Ratio] = mapped_column(nullable=False, default=Decimal(1))
    created_at: Mapped[datetime] = utcnow_column()

    __table_args__ = (
        Index("ix_research_evidence_hypothesis", "hypothesis_id", "role"),
    )


__all__ = [
    "CollectionPermit",
    "HypothesisEvidence",
    "ResearchHypothesis",
    "ResearchObservation",
    "ResearchSignal",
    "ResearchSource",
]
