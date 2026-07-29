"""Идеи (engine-ТЗ §23, блок Ideas).

Сканирование работает: конвейер §7 связывает данные, детекторы, три стратегии
§10–§12, оценку §15.1 и риск §17.

Эндпоинты, за которыми движка ещё нет, по-прежнему отвечают 503 с внятной
причиной, а не пустым списком: пустой список читается как «сегодня нет
сетапов», и это ложь, если считать было нечем.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from pydantic import Field
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Instrument, TradeIdea
from ...models.enums import IdeaStatus, QualityStatus, Strategy
from ...schemas.common import ApiModel
from ...schemas.ideas import (
    DailyCards,
    ExplanationBlock,
    IdeaDetail,
    IdeaEventOut,
    IdeaSummary,
    PlanBlock,
    ProbabilityBlock,
    ScoreBlock,
    ScoreComponent,
    SizingBlock,
    SkipRequest,
)

router = APIRouter(tags=["ideas"])

ENGINE_NOT_READY = (
    "движок исполнения ещё не подключён: бумажное сопровождение сделки "
    "(engine-ТЗ §21) реализуется отдельным этапом."
)


def _summary(idea: TradeIdea, symbol: str = "") -> IdeaSummary:
    return IdeaSummary(
        id=idea.id,
        instrument_id=idea.instrument_id,
        symbol=symbol,
        strategy=idea.strategy,
        direction=idea.direction,
        status=idea.status,
        quality_status=idea.quality_status,
        horizon_days=idea.horizon_days,
        score=idea.score,
        p_tp1_before_sl=idea.p_tp1_before_sl,
        confidence=idea.confidence,
        expected_r=idea.expected_r,
        rr_tp2=idea.rr_tp2,
        risk_amount=idea.risk_amount,
        signal_time=idea.signal_time,
        expires_at=idea.expires_at,
    )


def _confidence_band(value) -> str:
    v = float(value)
    if v < 0.45:
        return "LOW"
    return "HIGH" if v > 0.70 else "MEDIUM"


@router.get("/ideas", response_model=list[IdeaSummary])
def list_ideas(
    status: IdeaStatus | None = None,
    quality_status: QualityStatus | None = None,
    strategy: Strategy | None = None,
    presented_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[IdeaSummary]:
    """Лента идей.

    По умолчанию отдаёт **все** идеи, включая непоказанные: UX-ТЗ §12 требует
    хранить и отдавать их, иначе журнал не отвечает на вопрос, что система
    нашла, но не показала.
    """
    stmt = select(TradeIdea).order_by(TradeIdea.signal_time.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(TradeIdea.status == status)
    if quality_status is not None:
        stmt = stmt.where(TradeIdea.quality_status == quality_status)
    if strategy is not None:
        stmt = stmt.where(TradeIdea.strategy == strategy)
    if presented_only:
        stmt = stmt.where(TradeIdea.was_presented.is_(True))
    return [_summary(row) for row in db.execute(stmt).scalars()]


@router.get("/ideas/today", response_model=DailyCards)
def today(db: Session = Depends(get_db)) -> DailyCards:
    """Карточки дня (§16): до трёх, с явной причиной, если торговать нечего."""
    now = datetime.now(UTC)
    rows = list(
        db.execute(
            select(TradeIdea)
            .where(TradeIdea.expires_at > now, TradeIdea.was_presented.is_(True))
            .order_by(TradeIdea.presentation_rank)
        ).scalars()
    )
    trade_now = [_summary(r) for r in rows if r.quality_status == QualityStatus.ACTIVE]
    waiting = [_summary(r) for r in rows if r.quality_status == QualityStatus.WATCH]
    reason = ""
    if not trade_now and not waiting:
        reason = (
            "готовых сделок нет. Это валидный результат: приложение не создаёт "
            "сделки ради нормы (engine-ТЗ §0.7, §32)."
        )
    return DailyCards(
        generated_at=now,
        trade_now=trade_now,
        wait_for_trigger=waiting,
        no_trade_reason=reason,
    )


@router.get("/ideas/{idea_id}", response_model=IdeaDetail)
def get_idea(idea_id: UUID, db: Session = Depends(get_db)) -> IdeaDetail:
    idea = db.get(TradeIdea, idea_id)
    if idea is None:
        raise HTTPException(404, "идея не найдена")
    instrument = db.execute(
        select(Instrument).where(Instrument.instrument_id == idea.instrument_id)
    ).scalar_one_or_none()

    explanation = idea.explanation_json or {}
    breakdown = explanation.get("score_breakdown") or []
    base = _summary(idea, symbol=instrument.symbol if instrument else "")
    return IdeaDetail(
        **base.model_dump(),
        context_timeframe=idea.context_timeframe,
        setup_timeframe=idea.setup_timeframe,
        trigger_timeframe=idea.trigger_timeframe,
        plan=PlanBlock(
            order_intent=idea.order_intent,
            entry_low=idea.entry_low,
            entry_high=idea.entry_high,
            entry_reference=idea.entry_reference,
            stop=idea.stop,
            tp1=idea.tp1,
            tp2=idea.tp2,
            tp3=idea.tp3,
            rr_tp1=idea.rr_tp1,
            rr_tp2=idea.rr_tp2,
            invalidation=idea.invalidation,
        ),
        probability=ProbabilityBlock(
            p_tp1_before_sl=idea.p_tp1_before_sl,
            p_tp2_before_sl=idea.p_tp2_before_sl,
            p_positive_r_after_costs=idea.p_positive_r_after_costs,
            expected_r=idea.expected_r,
            confidence=idea.confidence,
            confidence_band=_confidence_band(idea.confidence),
            sample_size=idea.sample_size,
            source=idea.probability_source,
            capped=idea.probability_source == "rule_prior",
            cap_reason=(
                "статистики недостаточно: вероятность ограничена сверху "
                "правилом §15.3, пока не накоплено 100 релевантных "
                "OOS-сделок"
                if idea.probability_source == "rule_prior"
                else ""
            ),
        ),
        sizing=SizingBlock(
            risk_pct=idea.risk_pct,
            risk_amount=idea.risk_amount,
            quantity=idea.quantity,
            risk_per_unit=idea.risk_per_unit,
            drawdown_multiplier=idea.drawdown_multiplier,
            binding_limit=explanation.get("binding_limit", "none"),
            correlation_cluster=idea.correlation_cluster,
            tradable=idea.quantity > 0,
            not_tradable_reason=(
                "" if idea.quantity > 0
                else "объём меньше минимального лота: идея информационная "
                     "и не подтверждается (§20.1)"
            ),
        ),
        score_breakdown=ScoreBlock(
            total=idea.score,
            data_quality=idea.data_quality,
            components=[ScoreComponent(**c) for c in breakdown],
        ),
        explanation=ExplanationBlock(
            headline=explanation.get("headline", ""),
            thesis=explanation.get("thesis", ""),
            market_regime=explanation.get("market_regime", {}),
            timeframes=explanation.get("timeframes", []),
            supporting_factors=explanation.get("supporting_factors", []),
            counter_factors=explanation.get("counter_factors", []),
            invalidation=idea.invalidation,
            data_warnings=list(idea.data_warnings or []),
        ),
        config_hash=idea.config_hash,
        engine_version=idea.engine_version,
        feature_version=idea.feature_version,
        was_presented=idea.was_presented,
    )


@router.get("/ideas/{idea_id}/events", response_model=list[IdeaEventOut])
def get_events(idea_id: UUID, db: Session = Depends(get_db)) -> list[IdeaEventOut]:
    """Полная история переходов (§18). Только чтение: журнал неизменяем."""
    idea = db.get(TradeIdea, idea_id)
    if idea is None:
        raise HTTPException(404, "идея не найдена")
    return [IdeaEventOut.model_validate(e) for e in idea.events]


class ScanReport(ApiModel):
    """Отчёт о сканировании (§16).

    Отказы возвращаются вместе с идеями намеренно. «Сегодня ничего нет» без
    списка причин неотличимо от сломанного движка, а это разные решения —
    ждать или чинить.
    """

    started_at: datetime
    finished_at: datetime
    scanned: int
    produced: int
    trade_now: list[IdeaSummary] = Field(default_factory=list)
    wait_for_trigger: list[IdeaSummary] = Field(default_factory=list)
    no_trade_reason: str = ""
    skipped: list[dict] = Field(default_factory=list)
    rejected: list[dict] = Field(default_factory=list)


@router.post("/ideas/scan", response_model=ScanReport)
def run_scan(db: Session = Depends(get_db)) -> ScanReport:
    """Прогнать вселенную по конвейеру §7 и записать найденное."""
    from ...pipeline.scan import scan as run_pipeline

    result = run_pipeline(db)
    daily = result.daily
    return ScanReport(
        started_at=result.started_at,
        finished_at=result.finished_at or datetime.now(UTC),
        scanned=result.scanned,
        produced=result.produced,
        trade_now=[
            _summary(next(i for i in result.ideas if str(i.id) == r.idea.idea_id))
            for r in (daily.trade_now if daily else [])
        ],
        wait_for_trigger=[
            _summary(next(i for i in result.ideas if str(i.id) == r.idea.idea_id))
            for r in (daily.wait_for_trigger if daily else [])
        ],
        no_trade_reason=daily.no_trade_reason if daily else "",
        skipped=[
            {"instrument_id": s.instrument_id, "stage": s.stage, "reason": s.reason}
            for s in result.skipped
        ],
        rejected=[
            {
                "strategy": r.strategy.value,
                "reason": r.reason,
                "failed": [c.name for c in r.failed],
            }
            for r in result.rejections
        ],
    )


@router.post("/ideas/{idea_id}/approve-paper", status_code=503)
def approve_paper(idea_id: UUID):
    raise HTTPException(
        503,
        "бумажное исполнение подключается вместе с движком исполнения "
        "(engine-ТЗ §21). Боевые заявки в этом режиме запрещены: "
        "paper_only=true.",
    )


@router.post("/ideas/{idea_id}/reject", status_code=503)
def reject(idea_id: UUID, body: SkipRequest):
    raise HTTPException(503, ENGINE_NOT_READY)
