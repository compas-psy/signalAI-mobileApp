"""Ранние сигналы: гипотезы и готовность источников (ТЗ Early Signals §18.2).

Что здесь есть и чего нет. Есть чтение гипотез и состояние источников. Нет
и не появится ничего, что отправляет заявку: §18.3 требует буквально «no
endpoint для выставления заявок», а §16.4 запрещает выдавать BUY, SELL,
гарантированную доходность и размер позиции. Гипотеза отвечает на вопрос
«что стоит изучить», а не «что купить».

Отдельный адрес готовности источников нужен по той же причине, по какой в
конвейере пакетов есть статус: пустая выдача сама по себе неинформативна.
«Гипотез нет, потому что рынок спокоен» и «гипотез нет, потому что ни один
источник не подключён» — разные новости, и вторая требует действия
владельца, а не ожидания.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import HypothesisEvidence, ResearchHypothesis
from ...models.enums import HypothesisState
from ...research.sources import CONNECT_ORDER, readiness, sync_registry
from ...schemas.common import ApiModel, Money

router = APIRouter(prefix="/research", tags=["research"])


class EvidenceOut(ApiModel):
    role: str
    claim: str = ""
    lineage_root_id: str = ""
    independence_group: str = ""
    confidence: Money = Decimal(1)


class HypothesisOut(ApiModel):
    """Гипотеза как самостоятельный объект аудита (§16.1).

    Поля ``executable``, ``side`` и ``quantity`` отсутствуют намеренно и
    добавлены быть не могут: их нечему было бы заполнять.
    """

    id: str
    version: int
    market: str
    entity_id: str
    instrument_id: str = ""
    symbol: str = ""
    title: str
    direction: str
    state: str
    state_label: str
    as_of: datetime
    expected_lag: str = ""

    evidence_score: Money = Decimal(0)
    economic_score: Money = Decimal(0)
    market_context_score: Money | None = None
    market_context_state: str = "unknown"
    research_priority: Money = Decimal(0)

    three_two_one: dict = Field(default_factory=dict)
    target_kpis: list = Field(default_factory=list)
    causal_path: dict = Field(default_factory=dict)
    fact_summary: list = Field(default_factory=list)
    alternative_explanations: list = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    falsifiers: list = Field(default_factory=list)
    missing_evidence: list = Field(default_factory=list)
    evidence: list[EvidenceOut] = Field(default_factory=list)

    state_reason: str = ""
    next_review_at: datetime | None = None
    expires_at: datetime | None = None


class SourceStateOut(ApiModel):
    source_id: str
    name: str
    status: str
    note: str


class ResearchStatusOut(ApiModel):
    """Почему выдача выглядит так, как выглядит."""

    total_sources: int = 0
    free_route: int = 0
    awaiting_terms_check: list[str] = Field(default_factory=list)
    manual_only: list[str] = Field(default_factory=list)
    unavailable: list[SourceStateOut] = Field(default_factory=list)
    connect_order: list[str] = Field(default_factory=list)
    hypotheses: int = 0
    reason: str = ""


class ResearchResponse(ApiModel):
    status: ResearchStatusOut
    hypotheses: list[HypothesisOut] = Field(default_factory=list)


# Как состояние называется на экране. Английские коды остаются в базе и в
# API, но владельцу показывается человеческое слово.
STATE_LABELS = {
    HypothesisState.OBSERVATION: "наблюдение",
    HypothesisState.EARLY_CANDIDATE: "ранний кандидат",
    HypothesisState.CONFIRMED: "подтверждённая гипотеза",
    HypothesisState.DILIGENCE_READY: "к углублённому анализу",
    HypothesisState.INVALIDATED: "опровергнута",
    HypothesisState.EXPIRED: "срок вышел",
    HypothesisState.REJECTED: "отклонена",
}

# Состояния, которые показываются по умолчанию: живые.
LIVE_STATES = (
    HypothesisState.OBSERVATION,
    HypothesisState.EARLY_CANDIDATE,
    HypothesisState.CONFIRMED,
    HypothesisState.DILIGENCE_READY,
)


def _evidence(session: Session, hypothesis_id) -> list[EvidenceOut]:
    rows = session.execute(
        select(HypothesisEvidence).where(
            HypothesisEvidence.hypothesis_id == hypothesis_id
        )
    ).scalars()
    return [
        EvidenceOut(
            role=str(row.role),
            claim=row.claim_text,
            lineage_root_id=row.lineage_root_id,
            independence_group=row.independence_group,
            confidence=row.confidence,
        )
        for row in rows
    ]


def _out(session: Session, row: ResearchHypothesis) -> HypothesisOut:
    return HypothesisOut(
        id=str(row.id),
        version=row.version,
        market=row.market,
        entity_id=row.entity_id,
        instrument_id=row.instrument_id,
        symbol=row.instrument_id.split(":")[-1] if row.instrument_id else "",
        title=row.title,
        direction=str(row.direction),
        state=str(row.state),
        state_label=STATE_LABELS.get(row.state, str(row.state)),
        as_of=row.as_of,
        expected_lag=row.expected_lag,
        evidence_score=row.evidence_score,
        economic_score=row.economic_score,
        market_context_score=row.market_context_score,
        market_context_state=row.market_context_state,
        research_priority=row.research_priority,
        three_two_one=row.three_two_one_json or {},
        target_kpis=row.target_kpis_json or [],
        causal_path=row.causal_path_json or {},
        fact_summary=row.fact_summary_json or [],
        alternative_explanations=row.alternative_explanations_json or [],
        risk_flags=[str(f) for f in (row.risk_flags or [])],
        falsifiers=row.falsifiers_json or [],
        missing_evidence=row.missing_evidence_json or [],
        evidence=_evidence(session, row.id),
        state_reason=row.state_reason,
        next_review_at=row.next_review_at,
        expires_at=row.expires_at,
    )


@router.get("/hypotheses", response_model=ResearchResponse)
def hypotheses(
    session: Session = Depends(get_db),
    market: str | None = Query(default=None),
    include_closed: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> ResearchResponse:
    """Гипотезы вместе с состоянием источников.

    Состояние едет всегда, даже когда гипотезы есть: список источников,
    ждущих проверки условий, — это то, что владелец может сделать сегодня,
    и прятать его до пустого экрана незачем.
    """
    query = select(ResearchHypothesis)
    if not include_closed:
        query = query.where(
            ResearchHypothesis.state.in_([s.value for s in LIVE_STATES])
        )
    if market:
        query = query.where(ResearchHypothesis.market == market)
    rows = list(
        session.execute(
            query.order_by(ResearchHypothesis.research_priority.desc()).limit(limit)
        ).scalars()
    )

    state = readiness(session)
    reason = ""
    if not rows:
        if state["free_route"] and state["awaiting_terms_check"]:
            reason = (
                f"бесплатные маршруты есть у {state['free_route']} источников, но "
                f"{len(state['awaiting_terms_check'])} из них ждут проверки условий "
                "использования — до неё сбор не запускается"
            )
        elif not state["free_route"]:
            reason = "ни один источник не подключён: собирать нечего"
        else:
            reason = "источники подключены, гипотез пока нет"

    return ResearchResponse(
        status=ResearchStatusOut(
            total_sources=state["total"],
            free_route=state["free_route"],
            awaiting_terms_check=state["awaiting_terms_check"],
            manual_only=state["manual_only"],
            unavailable=[
                SourceStateOut(
                    source_id=item["source_id"],
                    name=item["name"],
                    status=item["status"],
                    note=item["note"],
                )
                for item in state["paid_or_prohibited"]
            ],
            connect_order=state["connect_order"],
            hypotheses=len(rows),
            reason=reason,
        ),
        hypotheses=[_out(session, row) for row in rows],
    )


@router.post("/sources/sync", response_model=ResearchStatusOut)
def sync_sources(session: Session = Depends(get_db)) -> ResearchStatusOut:
    """Записать реестр источников из кода в базу.

    Управляющий вызов, а не сбор данных: ничего наружу не запрашивает.
    Правовой режим — зафиксированное решение, и живёт оно в репозитории.
    """
    sync_registry(session, now=datetime.now(UTC))
    session.commit()
    state = readiness(session)
    return ResearchStatusOut(
        total_sources=state["total"],
        free_route=state["free_route"],
        awaiting_terms_check=state["awaiting_terms_check"],
        manual_only=state["manual_only"],
        unavailable=[
            SourceStateOut(
                source_id=item["source_id"],
                name=item["name"],
                status=item["status"],
                note=item["note"],
            )
            for item in state["paid_or_prohibited"]
        ],
        connect_order=list(CONNECT_ORDER),
    )


__all__ = ["router"]
