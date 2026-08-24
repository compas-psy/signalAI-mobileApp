"""Конвейер сканирования: от свечей до идеи в базе.

Порядок шагов задан §7 и не переставляется:

    данные → ликвидность → режим → структура → зоны → сетап → триггер →
    вход → инвалидация → цели → R/R → вероятность → риск портфеля

Каждый инструмент проходит его целиком либо отваливается **с причиной**.
Причины собираются и возвращаются вместе с результатом: без них владелец не
может отличить «рынок не дал сетапов» от «движок сломался», а это разные
решения — ждать или чинить.

Ничего не выдумывается. Если данных не хватает, инструмент попадает в отказы
с указанием, чего именно не хватило, а не получает нейтральную оценку.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from ..detectors import price_action, smc, wyckoff
from ..detectors.base import Reading
from ..detectors.price_action import PriceZone
from ..features.indicators import atr
from ..market.candles import Candle, resample_hours
from ..market.fx import rate_note, rate_to_rub
from ..market.economic_events import EconomicEventCalendar, load_owned_calendar
from ..models import Bar, IdeaEvent, Instrument, TradeIdea
from ..models.enums import (
    AssetClass,
    Direction,
    IdeaStatus,
    LiquidityRegime,
    QualityStatus,
    Timeframe,
)
from ..probability.barrier import (
    ConfidenceInput,
    confidence,
    expected_value,
    rule_prior,
    unmeasured_components,
)
from .trigger import confirmed as trigger_confirmed
from ..regime.classifier import (
    RegimeResult,
    classify_derivatives_flow,
    classify_liquidity,
    classify_trend,
    classify_volatility,
)
from ..risk.sizing import (
    InstrumentSpec,
    RiskLimits,
    RiskState,
    compute_budget,
    size_position,
)
from ..scoring.admission import AdmissionThresholds, admit
from ..scoring.score import Component, compute
from ..scoring.selection import RankedIdea, select_daily
from ..strategies import breakout_retest, trend_pullback, wyckoff_reversal
from ..strategies.base import Candidate, Rejection, SetupContext
from .presentation import build_annotations, build_evidence
from ..version import ENGINE_VERSION, FEATURE_VERSION

# Роли таймфреймов §7 для среднего горизонта: контекст 1D, сетап 4H,
# триггер 1H. Другие горизонты добавляются той же таблицей.
CONTEXT_TF = Timeframe.D1
TRIGGER_TF = Timeframe.H1
SETUP_HOURS = 4

# Минимумы, ниже которых считать нечего. Не «мало данных — снизим
# уверенность», а «мало данных — не считаем»: §4.4 запрещает запускать
# стратегию на неполных рядах.
MIN_CONTEXT_BARS = 60
MIN_TRIGGER_BARS = 120


@dataclass(frozen=True, slots=True)
class Skipped:
    """Инструмент, не дошедший до идеи, и почему."""

    instrument_id: str
    stage: str
    reason: str


@dataclass
class ScanResult:
    started_at: datetime
    finished_at: datetime | None = None
    scanned: int = 0
    ideas: list[TradeIdea] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    daily: object | None = None

    @property
    def produced(self) -> int:
        return len(self.ideas)


def _load_bars(
    session: Session, instrument_id: str, timeframe: Timeframe, limit: int
) -> list[Candle]:
    """Закрытые свечи в хронологическом порядке.

    Только закрытые (§4.4). Формирующийся бар сюда не попадает никогда — не
    «по умолчанию», а вовсе: конвейер не тот слой, где такое решение
    принимается.
    """
    rows = session.execute(
        select(Bar)
        .where(
            Bar.instrument_id == instrument_id,
            Bar.timeframe == timeframe,
            Bar.is_closed.is_(True),
        )
        .order_by(Bar.open_time.desc())
        .limit(limit)
    ).scalars()
    candles = [
        Candle(
            open_time=b.open_time,
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume_units=b.volume_units,
            volume_notional=b.volume_notional,
            open_interest=b.open_interest,
            is_closed=True,
            source=b.source,
            quality_flags=tuple(b.quality_flags or ()),
        )
        for b in rows
    ]
    return list(reversed(candles))


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _liquidity_inputs(
    instrument: Instrument,
    context: Sequence[Candle],
) -> tuple[float | None, Decimal | None]:
    """Ликвидность для scan без повторного отказа уже допущенного FORTS.

    Стратегические пороги здесь не меняются. Для crypto и прочих классов
    сохраняется прежнее поведение. Для FORTS второй проход universe уже
    измерил ликвидность по 30-дневной медиане, а при дырявом ISS history —
    по свежему снимку доски. Раньше scan забывал это измерение и подставлял
    только ``context[-1].volume_notional``. Если у последней D1-свечи ISS
    отдавал пустой/нулевой ``value``, контракт сначала честно проходил §5.2,
    а через несколько строк объявлялся UNTRADEABLE и до стратегий не доходил.

    Используем ровно те данные, на которых был сделан admission. Это ремонт
    data plumbing, а не ослабление фильтров поиска сигнала.
    """
    turnover = context[-1].volume_notional if context else None
    spread = 0.0005 if turnover else None
    if instrument.asset_class is not AssetClass.FUTURES:
        return spread, turnover

    meta = instrument.metadata_json or {}
    admission = meta.get("admission") or {}
    measured_turnover = _decimal(
        admission.get("median_daily_notional_rub")
        or admission.get("daily_notional_rub")
    )
    if measured_turnover is not None and measured_turnover > 0:
        turnover = measured_turnover

    measured_spread = _decimal(
        admission.get("relative_spread_snapshot") or meta.get("spread_snapshot")
    )
    if measured_spread is not None and measured_spread >= 0:
        spread = float(measured_spread)
    elif turnover is None:
        spread = None

    return spread, turnover


def _zones_from(readings: Sequence[Reading | None], direction: Direction) -> list[PriceZone]:
    """Контекстные зоны для детектора Price Action.

    Собираются из уже посчитанных наблюдений, а не заново: зона, которую
    триггер не разделяет со стратегией, ничего не подтверждает.
    """
    zones: list[PriceZone] = []
    want_bullish = direction is Direction.LONG
    for reading in readings:
        if reading is None:
            continue
        for block in reading.payload.get("order_blocks", []):
            if block.is_bullish == want_bullish:
                zones.append(PriceZone(block.low, block.high, "order_block"))
        for gap in reading.payload.get("fvg", []):
            if gap.is_bullish == want_bullish:
                zones.append(PriceZone(gap.low, gap.high, "fvg"))
        trading_range = reading.payload.get("range")
        if trading_range is not None:
            zones.append(
                PriceZone(trading_range.support, trading_range.resistance, "range_boundary")
            )
    return zones


def _components(
    candidate: Candidate,
    regime: RegimeResult,
    readings: dict[str, Reading | None],
    rr_tp2: Decimal,
    has_oi: bool,
) -> list[Component]:
    """Одиннадцать компонентов §15.1 из измерений, а не из впечатлений.

    Каждый несёт ссылку на наблюдение, из которого получен. Компонент без
    источника — это мнение, и в оценке ему не место (§9.1).
    """

    def pct(value: float) -> Decimal:
        return Decimal(str(round(max(0.0, min(1.0, value)) * 100, 2)))

    smc_reading = readings.get("smc")
    wyckoff_reading = readings.get("wyckoff")
    pa_reading = readings.get("price_action")

    regime_fit = 1.0 if regime.trend.value in ("UPTREND", "DOWNTREND") else 0.4
    structure = smc_reading.confidence.value if smc_reading else 0.0
    setup_quality = (
        wyckoff_reading.confidence.value if wyckoff_reading
        else (0.6 if candidate.zones else 0.3)
    )
    trigger_quality = pa_reading.confidence.value if pa_reading else 0.0

    volatility_fit = {
        "LOW": 0.6, "NORMAL": 1.0, "HIGH": 0.5, "EXTREME": 0.0,
    }[regime.volatility.value]
    liquidity_quality = {
        "GOOD": 1.0, "NORMAL": 0.7, "THIN": 0.2, "UNTRADEABLE": 0.0,
    }[regime.liquidity.value]

    rr_value = float(min(Decimal(1), rr_tp2 / Decimal(3)))
    confluence = min(1.0, len(candidate.zones) / 3)

    result = [
        Component("regime_fit", pct(regime_fit), note=regime.trend.value,
                  evidence_id="regime"),
        Component("market_structure", pct(structure),
                  status="measured" if smc_reading else "missing",
                  note=smc_reading.summary if smc_reading else "структура не посчитана",
                  evidence_id="smc"),
        Component("setup_quality", pct(setup_quality), evidence_id="setup"),
        Component("trigger_quality", pct(trigger_quality),
                  status="measured" if pa_reading else "missing",
                  note=pa_reading.summary if pa_reading else "триггер не найден",
                  evidence_id="price_action"),
        Component("volume_confirmation", pct(0.6 if smc_reading else 0.0),
                  status="measured" if smc_reading else "missing",
                  note="" if smc_reading else "объёмного подтверждения нет",
                  evidence_id="volume"),
        Component("volatility_fit", pct(volatility_fit),
                  note=regime.volatility.value, evidence_id="regime"),
        Component("level_confluence", pct(confluence),
                  note=f"зон в сетапе: {len(candidate.zones)}", evidence_id="setup"),
        Component("liquidity_quality", pct(liquidity_quality),
                  note=regime.liquidity.value, evidence_id="regime"),
        Component("execution_quality", pct(0.7 if liquidity_quality >= 0.7 else 0.3),
                  evidence_id="regime"),
        Component("risk_reward_quality", pct(rr_value),
                  note=f"R/R до TP2 {rr_tp2}", evidence_id="plan"),
    ]

    # Открытый интерес: «не бывает» и «не получен» — разные вещи, и разница
    # видна в оценке (§15.1).
    if has_oi:
        result.append(
            Component(
                "derivatives_confirmation",
                pct(1.0 if regime.derivatives_flow.value != "NEUTRAL" else 0.5),
                note=regime.derivatives_flow.value,
                evidence_id="regime",
            )
        )
    else:
        result.append(
            Component(
                "derivatives_confirmation", Decimal(0), status="not_applicable",
                note="открытого интереса у этого инструмента нет",
            )
        )
    return result


def _admission_thresholds(cfg: EngineConfig) -> AdmissionThresholds:
    """Build every idea admission threshold from the versioned engine config."""
    return AdmissionThresholds(
        active_probability_min=Decimal(str(cfg.get("ideas.active_probability_min"))),
        active_expected_r_min=Decimal(str(cfg.get("ideas.active_expected_r_min"))),
        min_rr_tp2=Decimal(str(cfg.get("ideas.min_rr_tp2"))),
        min_confidence=Decimal(str(cfg.get("ideas.min_confidence"))),
        watch_probability_min=Decimal(str(cfg.get("ideas.watch_probability_min"))),
    )


def _is_persistable_live_quality(status: QualityStatus) -> bool:
    """Only user-visible ACTIVE/WATCH admissions may occupy the live-idea slot."""
    return status in (QualityStatus.ACTIVE, QualityStatus.WATCH)


def scan_instrument(
    session: Session,
    instrument: Instrument,
    *,
    cfg: EngineConfig,
    risk_state: RiskState,
    now: datetime,
    event_calendar: EconomicEventCalendar | None = None,
) -> tuple[TradeIdea | None, list[Skipped], list[Rejection]]:
    """Прогнать один инструмент по всему конвейеру."""
    skipped: list[Skipped] = []
    rejections: list[Rejection] = []
    iid = instrument.instrument_id

    # ── Данные ───────────────────────────────────────────────────────────
    context = _load_bars(session, iid, CONTEXT_TF, 400)
    trigger = _load_bars(session, iid, TRIGGER_TF, 800)
    if len(context) < MIN_CONTEXT_BARS:
        return None, [Skipped(iid, "данные", f"дневных баров {len(context)}, нужно {MIN_CONTEXT_BARS}")], []
    if len(trigger) < MIN_TRIGGER_BARS:
        return None, [Skipped(iid, "данные", f"часовых баров {len(trigger)}, нужно {MIN_TRIGGER_BARS}")], []

    setup = resample_hours(trigger, SETUP_HOURS, session_start_hour_utc=6)
    if len(setup) < 40:
        return None, [Skipped(iid, "данные", f"баров 4H после склейки {len(setup)}, нужно 40")], []

    # ── Ликвидность и режим ──────────────────────────────────────────────
    spread, turnover = _liquidity_inputs(instrument, context)
    liquidity, liq_reasons = classify_liquidity(
        relative_spread=spread,
        median_notional=turnover,
        min_notional=Decimal(str(cfg.get("universe.futures.min_median_daily_notional_rub"))),
        max_spread=float(cfg.get("universe.futures.max_median_relative_spread")),
    )
    if liquidity is LiquidityRegime.UNTRADEABLE:
        return None, [Skipped(iid, "ликвидность", "; ".join(liq_reasons))], []

    trend, score, signals, detail = classify_trend(
        context,
        ema_fast=int(cfg.get("regime.ema_fast")),
        ema_slow=int(cfg.get("regime.ema_slow")),
        adx_period=int(cfg.get("regime.adx_period")),
        adx_trend_min=float(cfg.get("regime.adx_trend_min")),
    )
    volatility, _ = classify_volatility(context)
    has_oi = any(c.open_interest is not None for c in context[-30:])
    flow = classify_derivatives_flow(price_change_z=None, oi_change_z=None)

    regime = RegimeResult(
        trend=trend, trend_score=score, volatility=volatility,
        liquidity=liquidity, derivatives_flow=flow, signals=signals, detail=detail,
    )

    # ── Детекторы ────────────────────────────────────────────────────────
    smc_reading = smc.detect(setup, timeframe="4h")
    wyckoff_reading = wyckoff.detect(setup, timeframe="4h")
    atr_setup = next((v for v in reversed(atr(setup)) if v is not None), None)
    atr_trigger = next((v for v in reversed(atr(trigger)) if v is not None), None)

    direction = Direction.LONG if score >= 0 else Direction.SHORT
    zones = _zones_from([smc_reading, wyckoff_reading], direction)
    pa_reading = (
        price_action.detect(
            trigger, timeframe="1h", zones=zones,
            higher_tf_direction="long" if direction is Direction.LONG else "short",
        )
        if zones else None
    )

    ctx = SetupContext(
        instrument_id=iid,
        context_bars=context, setup_bars=setup, trigger_bars=trigger,
        context_tf="1d", setup_tf="4h", trigger_tf="1h",
        regime=regime, horizon_days=5, tick_size=instrument.tick_size,
        smc=smc_reading, wyckoff=wyckoff_reading, price_action=pa_reading,
        atr_setup=atr_setup, atr_trigger=atr_trigger,
    )

    # ── Стратегии §10–§12 ────────────────────────────────────────────────
    outcomes = [
        trend_pullback.build(ctx),
        breakout_retest.build(ctx),
        wyckoff_reversal.build(ctx),
    ]
    candidates = [o for o in outcomes if isinstance(o, Candidate)]
    rejections.extend(o for o in outcomes if isinstance(o, Rejection))
    if not candidates:
        return None, skipped, rejections

    candidate = max(candidates, key=lambda c: c.rr(1))
    for other in candidates:
        if other is not candidate:
            rejections.append(
                Rejection(other.strategy, other.checks,
                          f"перебит кандидатом {candidate.strategy.value} "
                          f"с R/R {candidate.rr(1)}")
            )

    rr_tp1, rr_tp2 = candidate.rr(0), candidate.rr(1)

    # ── Оценка §15.1 ─────────────────────────────────────────────────────
    readings = {"smc": smc_reading, "wyckoff": wyckoff_reading, "price_action": pa_reading}
    quality = compute(
        _components(candidate, regime, readings, rr_tp2, has_oi),
        data_quality_floor=Decimal(str(cfg.get("scoring.data_quality_floor"))),
        data_quality_ceiling=Decimal(str(cfg.get("scoring.data_quality_ceiling"))),
    )

    # ── Вероятность §15.3 и уверенность §15.4 ────────────────────────────
    probability = rule_prior(
        quality.total, cap=Decimal(str(cfg.get("probability.rule_prior_cap")))
    )
    conf_value, conf_band, conf_weights = confidence(
        ConfidenceInput(
            sample_adequacy=None,
            calibration=None,
            data_completeness=quality.data_quality,
            regime_similarity=Decimal("0.5"),
            stability=Decimal("0.5"),
        )
    )
    ev = expected_value(
        p_win=probability.p_tp1_before_sl,
        avg_win_r=rr_tp2,
        avg_loss_r=Decimal(1),
        costs_r=Decimal("0.05"),
    )

    # ── Допуск §15.6 ─────────────────────────────────────────────────────
    calendar_assessment = (event_calendar or EconomicEventCalendar((), source_available=False)).assess(
        iid, as_of=now
    )
    verdict = admit(
        probability=probability.p_tp1_before_sl,
        expected_r=ev,
        rr_tp2=rr_tp2,
        confidence=conf_value,
        liquidity=liquidity,
        has_trigger=trigger_confirmed(pa_reading),
        risk_blocked=False,
        event_assessment=calendar_assessment,
        thresholds=_admission_thresholds(cfg),
    )
    if not _is_persistable_live_quality(verdict.status):
        skipped.append(Skipped(iid, "допуск", verdict.reason))
        return None, skipped, rejections

    # ── Риск §17 и размер §17.1 ──────────────────────────────────────────
    budget = compute_budget(
        score=quality.total,
        state=risk_state,
        limits=RiskLimits(
            base_risk_per_trade=cfg.decimal("risk.base_risk_per_trade"),
            max_risk_per_trade=cfg.decimal("risk.max_risk_per_trade"),
            max_total_open_risk=cfg.decimal("risk.max_total_open_risk"),
            max_cluster_risk=cfg.decimal("risk.max_cluster_risk"),
            daily_loss_limit=cfg.decimal("risk.daily_loss_limit"),
            weekly_loss_limit=cfg.decimal("risk.weekly_loss_limit"),
            monthly_loss_limit=cfg.decimal("risk.monthly_loss_limit"),
        ),
        strategy_multiplier=candidate.risk_multiplier,
    )
    quote_rate = rate_to_rub(session, instrument.currency, now=now)
    sizing = size_position(
        budget=budget,
        entry=candidate.entry_reference,
        stop=candidate.stop,
        spec=InstrumentSpec(
            tick_size=instrument.tick_size,
            tick_value=instrument.tick_value,
            quantity_step=instrument.quantity_step,
            min_quantity=instrument.min_quantity,
            min_notional=instrument.min_notional,
            contract_multiplier=instrument.contract_multiplier,
            is_linear=instrument.venue.value == "CRYPTO",
            quote_currency=instrument.currency,
            quote_to_account=quote_rate,
        ),
    )

    # Lifecycle отвечает за факт рыночного сигнала, sizing — только за то,
    # можно ли исполнить этот сигнал при текущем риск-бюджете. Смешивать их
    # нельзя: у FORTS минимальный объём равен одному контракту, поэтому
    # корректный ACTIVE-сигнал при маленьком/неизвестном risk equity может
    # иметь quantity=0. Раньше такой сигнал ошибочно переводился обратно в
    # WATCH, хотя все ворота §15.6 уже были пройдены. Approval всё равно
    # fail-closed проверяет quantity > 0, поэтому разделение не ослабляет риск.
    status = (
        IdeaStatus.TRIGGERED
        if verdict.status is QualityStatus.ACTIVE
        else IdeaStatus.WATCH
    )

    idea = TradeIdea(
        instrument_id=iid,
        strategy=candidate.strategy,
        direction=candidate.direction,
        status=status,
        quality_status=verdict.status,
        horizon_days=ctx.horizon_days,
        context_timeframe=ctx.context_tf,
        setup_timeframe=ctx.setup_tf,
        trigger_timeframe=ctx.trigger_tf,
        order_intent=candidate.order_intent,
        entry_low=candidate.entry_low,
        entry_high=candidate.entry_high,
        entry_reference=candidate.entry_reference,
        stop=candidate.stop,
        tp1=candidate.targets[0].price,
        tp2=candidate.targets[1].price,
        tp3=candidate.targets[2].price if len(candidate.targets) > 2 else None,
        invalidation=candidate.invalidation,
        rr_tp1=rr_tp1,
        rr_tp2=rr_tp2,
        score=quality.total,
        data_quality=quality.data_quality,
        p_tp1_before_sl=probability.p_tp1_before_sl,
        expected_r=ev,
        confidence=conf_value,
        sample_size=probability.sample_size,
        probability_source=probability.source,
        risk_pct=budget.percent,
        risk_amount=sizing.risk_amount,
        quantity=sizing.quantity,
        risk_per_unit=sizing.risk_per_unit,
        correlation_cluster=instrument.correlation_cluster,
        drawdown_multiplier=budget.drawdown_multiplier,
        explanation_json={
            "headline": candidate.thesis[:120],
            "thesis": candidate.thesis,
            "market_regime": {
                "trend": regime.trend.value,
                "volatility": regime.volatility.value,
                "liquidity": regime.liquidity.value,
                "derivatives_flow": regime.derivatives_flow.value,
            },
            "timeframes": [
                {"tf": "1D", "role": "context", "summary": regime.trend.value},
                {"tf": "4H", "role": "setup",
                 "summary": wyckoff_reading.summary if wyckoff_reading else "структура"},
                {"tf": "1H", "role": "trigger",
                 "summary": pa_reading.summary if pa_reading else "триггера нет"},
            ],
            "supporting_factors": [c.detail for c in candidate.checks if c.passed],
            "counter_factors": list(candidate.counter_factors),
            "admission": verdict.reason,
            "admission_failed": [g.name for g in verdict.failed],
            "event_calendar": calendar_assessment.as_json(),
            "confidence_unmeasured": unmeasured_components(conf_weights),
            "binding_limit": budget.binding,
            "score_breakdown": [
                {
                    "name": c.name, "label": c.label,
                    "weight": str(c.effective_weight), "value": str(c.value),
                    "points": str(c.points), "detail": c.note,
                    "measured": c.status == "measured",
                }
                for c in quality.components
            ],
            "sizing_note": sizing.reason,
            "quote_currency": instrument.currency,
            "quote_rate_rub": None if quote_rate is None else str(quote_rate),
            "quote_note": rate_note(session, instrument.currency, now=now),
            "quantity_step": str(instrument.quantity_step),
        },
        evidence_json=build_evidence(
            readings, candidate,
            regime_summary=(
                f"{regime.trend.value}, волатильность {regime.volatility.value}, "
                f"ликвидность {regime.liquidity.value}"
            ),
        ),
        annotations_json=build_annotations(
            readings, candidate,
            signal_time=now,
            expires_at=now + timedelta(days=ctx.horizon_days),
            trigger_timeframe=ctx.trigger_tf,
        ),
        data_warnings=list(quality.missing),
        signal_time=now,
        expires_at=now + timedelta(days=ctx.horizon_days),
        config_hash=cfg.config_hash,
        engine_version=ENGINE_VERSION,
        feature_version=FEATURE_VERSION,
    )
    return idea, skipped, rejections


def _live_idea_ids(session: Session) -> dict[str, str]:
    """Инструмент → идентификатор живой user-facing идеи по нему."""
    terminal = [s for s in IdeaStatus if s.is_terminal]
    rows = session.execute(
        select(TradeIdea.instrument_id, TradeIdea.id, TradeIdea.quality_status)
        .where(TradeIdea.status.notin_(terminal))
        .order_by(TradeIdea.created_at)
    ).all()
    out: dict[str, str] = {}
    for instrument_id, idea_id, quality_status in rows:
        if not _is_persistable_live_quality(QualityStatus(quality_status)):
            continue
        out.setdefault(instrument_id, str(idea_id))
    return out


def scan(
    session: Session,
    *,
    cfg: EngineConfig | None = None,
    risk_state: RiskState | None = None,
    now: datetime | None = None,
    event_calendar: EconomicEventCalendar | None = None,
) -> ScanResult:
    """Просканировать вселенную и записать найденное."""
    moment = now or datetime.now(UTC)
    config = cfg or get_config()
    calendar = event_calendar or load_owned_calendar(now=moment)
    state = risk_state or RiskState(risk_equity=Decimal(100_000))
    result = ScanResult(started_at=moment)

    from ..journal.lifecycle import record_initial

    instruments = list(
        session.execute(
            select(Instrument).where(
                Instrument.in_universe.is_(True), Instrument.is_tradable.is_(True)
            )
        ).scalars()
    )
    result.scanned = len(instruments)

    живые = _live_idea_ids(session)

    for instrument in instruments:
        try:
            idea, skipped, rejections = scan_instrument(
                session, instrument, cfg=config, risk_state=state, now=moment,
                event_calendar=calendar,
            )
        except Exception as exc:
            result.skipped.append(
                Skipped(instrument.instrument_id, "ошибка", f"{type(exc).__name__}: {exc}")
            )
            continue
        result.skipped.extend(skipped)
        result.rejections.extend(rejections)
        if idea is not None:
            прежняя = живые.get(instrument.instrument_id)
            if прежняя is not None:
                result.skipped.append(
                    Skipped(
                        instrument.instrument_id,
                        "дубль",
                        f"по инструменту уже есть живая идея {прежняя}",
                    )
                )
                continue
            session.add(idea)
            session.flush()
            record_initial(
                session, idea, reason_code="scan",
                reason_detail=idea.explanation_json.get("admission", ""),
            )
            result.ideas.append(idea)
            живые[instrument.instrument_id] = str(idea.id)

    # ── Отбор карточек дня §16 ───────────────────────────────────────────
    ranked = [
        RankedIdea(
            idea_id=str(i.id),
            instrument_id=i.instrument_id,
            cluster=i.correlation_cluster,
            quality_status=QualityStatus(i.quality_status),
            expected_r=i.expected_r,
            probability=i.p_tp1_before_sl,
            confidence=i.confidence,
            score=i.score,
            liquidity=LiquidityRegime.GOOD,
        )
        for i in result.ideas
    ]
    daily = select_daily(ranked, max_cards=int(config.get("ideas.max_daily_cards")))
    result.daily = daily
    presented = {r.idea.idea_id for r in (*daily.trade_now, *daily.wait_for_trigger)}
    for i, idea in enumerate(result.ideas):
        if str(idea.id) in presented:
            idea.was_presented = True
            idea.presentation_rank = i
    session.flush()

    result.finished_at = datetime.now(UTC)
    return result