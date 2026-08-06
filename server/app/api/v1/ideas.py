"""Идеи (engine-ТЗ §23, блок Ideas).

Сканирование работает: конвейер §7 связывает данные, детекторы, три стратегии
§10–§12, оценку §15.1 и риск §17. Явное решение владельца либо создаёт
paper-сделку для круглосуточного серверного сопровождения, либо остаётся в
неизменяемой истории как отказ. Боевых заявок этот модуль не отправляет.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...journal.lifecycle import TransitionRequest, can_transition, transition
from ...models import IdeaEvent, IdeaSkip, Instrument, PaperTrade, TradeIdea
from ...models.enums import IdeaStatus, QualityStatus, SkipReason, Strategy
from ...paper.tracker import PaperTradeConflict, approve_for
from ...schemas.common import ApiModel
from ...schemas.ideas import (
    AnnotationOut,
    DailyCards,
    EvidenceOut,
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
from .paper import PaperTradeOut
from .paper import _out as _paper_out

router = APIRouter(tags=["ideas"])

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


def _closing_reason(db: Session, idea: TradeIdea) -> str:
    """Почему идея закончилась — дословно из журнала переходов.

    Состояние отвечает «что», а не «почему». «Рынок обогнал» на экране — это
    отметка; владельцу нужно прочитать, что цена дошла до дальней цели, ни
    разу не зайдя в зону входа. Пересказывать это в интерфейсе нельзя:
    журнал неизменяем, а пересказ разошёлся бы с ним на первой же правке.
    """
    if not IdeaStatus(idea.status).is_terminal:
        return ""
    row = db.execute(
        select(IdeaEvent.reason_detail, IdeaEvent.reason_code)
        .where(IdeaEvent.idea_id == idea.id)
        .order_by(IdeaEvent.sequence.desc())
        .limit(1)
    ).first()
    if row is None:
        return ""
    detail, code = row
    return str(detail or code or "")


def _decimal_or_none(raw) -> Decimal | None:
    """Число из объяснения. Курс хранится строкой — он и есть Decimal."""
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _quantity_unit(instrument: Instrument | None) -> str:
    """Чем меряется объём: монетой, контрактом или бумагой.

    Подпись обязана быть настоящей. «28 шт.» под бессрочным контрактом на
    эфир и «0,348 ETH» — это не разное оформление одного числа, а разные
    утверждения о том, что владелец собирается купить.
    """
    if instrument is None:
        return ""
    venue = getattr(instrument.venue, "value", str(instrument.venue))
    if venue == "CRYPTO":
        symbol = instrument.symbol.upper()
        quote = (instrument.currency or "").upper()
        base = symbol[: -len(quote)] if quote and symbol.endswith(quote) else symbol
        return base or symbol
    asset = getattr(instrument.asset_class, "value", str(instrument.asset_class))
    return "контр." if asset == "FUTURES" else "шт."


def _sizing_block(
    idea: TradeIdea, instrument: Instrument | None, explanation: dict
) -> SizingBlock:
    """Размер позиции вместе с тем, в чём он измерен.

    Отдельная функция, а не выражение внутри карточки: здесь же ловится
    идея, посчитанная до появления курса (§17.1). У такой объём получен
    делением рублёвого бюджета на риск в USDT — число правдоподобное и
    неверное в десятки раз. Пересчитывать её нельзя: идея неизменяема, по
    ней уже могли принять решение. Значит, она перестаёт быть исполнимой и
    прямо говорит почему.
    """
    currency = explanation.get("quote_currency") or (
        instrument.currency if instrument else "RUB"
    )
    # Признак старого расчёта: валюта котировки не рублёвая, а в объяснении
    # нет курса — значит его тогда и не спрашивали.
    stale_fx = (
        str(currency).upper() != "RUB" and "quote_currency" not in explanation
    )
    tradable = idea.quantity > 0 and not stale_fx
    if stale_fx:
        reason = (
            f"объём посчитан до пересчёта валют: бюджет в рублях делился на "
            f"риск в {currency}. Идея остаётся наблюдением — новая посчитается "
            "с курсом (§17.1)"
        )
    elif idea.quantity > 0:
        reason = ""
    else:
        # Причина берётся с расчёта, а не выводится из нуля: «объём меньше
        # лота» и «курс USDT к рублю неизвестен» требуют разных действий,
        # а выглядят одинаково — пустым размером.
        reason = explanation.get("sizing_note") or (
            "объём меньше минимального лота: идея информационная "
            "и не подтверждается (§20.1)"
        )
    return SizingBlock(
        risk_pct=idea.risk_pct,
        risk_amount=idea.risk_amount,
        quantity=idea.quantity,
        risk_per_unit=idea.risk_per_unit,
        drawdown_multiplier=idea.drawdown_multiplier,
        binding_limit=explanation.get("binding_limit", "none"),
        correlation_cluster=idea.correlation_cluster,
        quantity_step=instrument.quantity_step if instrument else Decimal(1),
        quantity_unit=_quantity_unit(instrument),
        quote_currency=currency,
        quote_rate_rub=_decimal_or_none(explanation.get("quote_rate_rub")),
        quote_note=explanation.get("quote_note", ""),
        tradable=tradable,
        not_tradable_reason=reason,
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
    rows = [
        r
        for r in db.execute(
            select(TradeIdea)
            .where(TradeIdea.expires_at > now, TradeIdea.was_presented.is_(True))
            .order_by(TradeIdea.presentation_rank)
        ).scalars()
        # Терминальная идея — не предложение. Здесь проверялся только срок,
        # и убитая kill-условием идея с непросроченным expires_at
        # оставалась в карточках дня: владелец видел «сделку», по которой
        # входить уже некуда. Журнал её хранит, лента дня — нет.
        if not IdeaStatus(r.status).is_terminal
    ]
    trade_now = [_summary(row) for row in rows if _is_actionable(row)]
    waiting = [
        _summary(row)
        for row in rows
        if QualityStatus(row.quality_status)
        in (QualityStatus.ACTIVE, QualityStatus.WATCH)
        and not _is_actionable(row)
    ]
    reason = ""
    if not trade_now and not waiting:
        reason = (
            "готовых сделок нет. Это валидный результат: приложение не создаёт "
            "сделки ради нормы (engine-ТЗ §0.7, §32)."
        )
    elif not trade_now:
        reason = (
            "готовых к исполнению сделок нет — показаны кандидаты, "
            "ожидающие триггера"
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
        sizing=_sizing_block(idea, instrument, explanation),
        evidence=[EvidenceOut(**e) for e in (idea.evidence_json or [])],
        annotations=[AnnotationOut(**a) for a in (idea.annotations_json or [])],
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
        closing_reason=_closing_reason(db, idea),
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


class IdeaDecisionOut(ApiModel):
    """Единый результат явного решения владельца по торговой идее."""

    idea_id: UUID
    decision: Literal["APPROVED_PAPER", "REJECTED"]
    idea_status: IdeaStatus
    # Этот контракт намеренно не допускает LIVE-варианта. Когда-нибудь для
    # него понадобится другой endpoint и отдельное решение владельца.
    paper_only: Literal[True] = True
    idempotent_replay: bool = False
    decided_at: datetime
    reason: SkipReason | None = None
    comment: str = ""
    trade: PaperTradeOut | None = None


def _locked_idea(db: Session, idea_id: UUID) -> TradeIdea:
    """Прочитать идею с блокировкой решений approve/reject между собой."""
    idea = db.execute(
        select(TradeIdea).where(TradeIdea.id == idea_id).with_for_update()
    ).scalar_one_or_none()
    if idea is None:
        raise HTTPException(404, "идея не найдена")
    return idea


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _existing_trade(db: Session, idea_id: UUID) -> PaperTrade | None:
    # В старой схеме уникальность была только для живых строк. ``first``
    # делает повтор безопасным и на базе, где исторически мог остаться дубль:
    # владелец всегда получает самую первую, а не случайную запись.
    return db.execute(
        select(PaperTrade)
        .where(PaperTrade.idea_id == idea_id)
        .order_by(PaperTrade.created_at, PaperTrade.id)
    ).scalars().first()


def _approval_response(
    idea: TradeIdea,
    trade: PaperTrade,
    *,
    replay: bool,
    now: datetime,
) -> IdeaDecisionOut:
    return IdeaDecisionOut(
        idea_id=idea.id,
        decision="APPROVED_PAPER",
        idea_status=idea.status,
        paper_only=True,
        idempotent_replay=replay,
        decided_at=trade.created_at,
        trade=_paper_out(trade, now=now),
    )


def _is_actionable(idea: TradeIdea) -> bool:
    """Единый статусный допуск для карточки и approve-paper."""
    return (
        QualityStatus(idea.quality_status) is QualityStatus.ACTIVE
        and IdeaStatus(idea.status) in (IdeaStatus.TRIGGERED, IdeaStatus.ACTIVE)
    )


def _validate_approval(idea: TradeIdea, *, now: datetime) -> None:
    """Fail-closed допуск: только подтверждённая активная свежая идея."""
    quality = QualityStatus(idea.quality_status)
    status = IdeaStatus(idea.status)
    if quality is not QualityStatus.ACTIVE:
        raise HTTPException(
            409,
            f"идея не actionable: quality_status={quality.value}; "
            "approve-paper разрешён только для ACTIVE",
        )
    if not _is_actionable(idea):
        raise HTTPException(
            409,
            f"идея не подтверждена рынком: status={status.value}; "
            "ожидается TRIGGERED или ACTIVE",
        )
    if _utc(idea.expires_at) <= now:
        raise HTTPException(409, "идея устарела: expires_at уже наступил")
    if idea.quantity <= 0:
        raise HTTPException(409, "идея не исполнима: размер позиции равен нулю")


@router.post(
    "/ideas/{idea_id}/approve-paper",
    response_model=IdeaDecisionOut,
)
def approve_paper(
    idea_id: UUID, db: Session = Depends(get_db)
) -> IdeaDecisionOut:
    """Явно подтвердить идею и передать её серверному paper-сопровождению.

    Здесь нет ветки, способной отправить live-заявку: результатом всегда
    является только строка ``paper_trades``. Повтор возвращает ту же строку,
    поэтому повторный тап, retry клиента и сетевой timeout не умножают риск.
    """
    now = datetime.now(UTC)
    idea = _locked_idea(db, idea_id)

    existing = _existing_trade(db, idea.id)
    if existing is not None:
        return _approval_response(idea, existing, replay=True, now=now)

    skipped = db.get(IdeaSkip, idea.id)
    if skipped is not None:
        raise HTTPException(409, "идея уже отклонена владельцем")
    _validate_approval(idea, now=now)

    try:
        trade, created = approve_for(db, idea, now=now)
    except PaperTradeConflict as exc:
        raise HTTPException(
            409,
            f"по инструменту уже ведётся другая paper-сделка {exc.trade.id}",
        ) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None

    # TRIGGERED означает «рыночный триггер подтверждён». ACTIVE начинается
    # только после решения владельца; это отделяет сигнал от исполнения.
    if created and IdeaStatus(idea.status) is IdeaStatus.TRIGGERED:
        transition(
            db,
            idea,
            TransitionRequest(
                new_status=IdeaStatus.ACTIVE,
                reason_code="user_approved_paper",
                reason_detail="владелец подтвердил автономное paper-сопровождение",
                market_snapshot={"paper_trade_id": str(trade.id)},
                user_action=True,
            ),
        )
    db.flush()
    return _approval_response(idea, trade, replay=not created, now=now)


def _rejection_response(
    idea: TradeIdea, skipped: IdeaSkip, *, replay: bool
) -> IdeaDecisionOut:
    return IdeaDecisionOut(
        idea_id=idea.id,
        decision="REJECTED",
        idea_status=idea.status,
        paper_only=True,
        idempotent_replay=replay,
        decided_at=skipped.skipped_at,
        reason=skipped.reason,
        comment=skipped.comment,
    )


@router.post(
    "/ideas/{idea_id}/reject",
    response_model=IdeaDecisionOut,
)
def reject(
    idea_id: UUID, body: SkipRequest, db: Session = Depends(get_db)
) -> IdeaDecisionOut:
    """Неизменно записать отказ владельца, не удаляя идею и её историю."""
    idea = _locked_idea(db, idea_id)
    existing = db.get(IdeaSkip, idea.id)
    if existing is not None:
        return _rejection_response(idea, existing, replay=True)

    if _existing_trade(db, idea.id) is not None:
        raise HTTPException(
            409,
            "по идее уже создана paper-сделка; используйте закрытие сделки",
        )

    status = IdeaStatus(idea.status)
    if not can_transition(status, IdeaStatus.CANCELLED):
        raise HTTPException(
            409,
            f"идею в состоянии {status.value} уже нельзя отклонить",
        )

    skipped = IdeaSkip(
        idea_id=idea.id,
        reason=body.reason,
        comment=body.comment,
        snapshot_json={
            "status": status.value,
            "quality_status": str(idea.quality_status),
            "score": str(idea.score),
            "p_tp1_before_sl": str(idea.p_tp1_before_sl),
            "expected_r": str(idea.expected_r),
            "expires_at": _utc(idea.expires_at).isoformat(),
        },
    )
    db.add(skipped)
    transition(
        db,
        idea,
        TransitionRequest(
            new_status=IdeaStatus.CANCELLED,
            reason_code="user_rejected",
            reason_detail=(
                f"{body.reason.value}: {body.comment}"
                if body.comment
                else body.reason.value
            )[:512],
            user_action=True,
        ),
    )
    db.flush()
    return _rejection_response(idea, skipped, replay=False)
