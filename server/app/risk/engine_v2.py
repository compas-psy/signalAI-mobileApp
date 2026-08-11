"""Adaptive Risk Engine v2 — единственная runtime-точка расчёта риска.

Этот слой намеренно ставится только в scheduler-процессе через
``pipeline.risk_equity_runtime.install``. API его не считает: API лишь
подписывает уже созданный план. Это исключает две независимо живущие версии
sizing-логики.

Модель оставляет абсолютные лимиты §17 неизменными. Она умеет только
*уменьшать* разрешённый риск внутри этих лимитов по качеству статистики,
стабильности WinRate и confidence. Исторические исходы используются через
байесовское shrinkage: маленькая выборка не превращается в ложную точность.

LLM сюда не допущен. Числа риска считает детерминированный код; локальная
модель используется optimizer-ом только как критик уже рассчитанного
challenger-а.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_DOWN
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from ..models import IdeaOutcome, PaperTrade, TradeIdea
from ..models.enums import BarrierOutcome, PaperStatus
from ..probability.barrier import ConfidenceInput, ProbabilityEstimate, empirical
from .sizing import RiskState


@dataclass(frozen=True, slots=True)
class EdgeSnapshot:
    sample_size: int = 0
    wins: int = 0
    posterior_win: Decimal = Decimal("0.5")
    sample_adequacy: Decimal = Decimal(0)
    calibration: Decimal | None = None
    stability: Decimal = Decimal("0.5")
    source: str = "rule_prior"


@dataclass(slots=True)
class RuntimeContext:
    session: Session
    instrument: Any
    cfg: EngineConfig
    edge: EdgeSnapshot
    confidence: Decimal = Decimal("0.5")
    mode: str = "defensive"
    profile: dict[str, Any] | None = None


_CTX: ContextVar[RuntimeContext | None] = ContextVar("signalai_risk_v2", default=None)
_INSTALLED = False

# Захватываются один раз при install(). Это математические примитивы старого
# слоя; после установки ни один scheduler-call не обращается к ним напрямую.
_ORIGINAL_SCAN_INSTRUMENT = None
_ORIGINAL_COMPUTE_BUDGET = None
_ORIGINAL_CONFIDENCE = None
_ORIGINAL_RULE_PRIOR = None
_ORIGINAL_EXPECTED_VALUE = None
_ORIGINAL_TRACK = None


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


def _clamp(value: Decimal, low: Decimal = Decimal(0), high: Decimal = Decimal(1)) -> Decimal:
    return max(low, min(high, value))


def _edge_snapshot(session: Session, instrument: Any) -> EdgeSnapshot:
    """Иерархическая оценка edge по релевантным OOS-исходам.

    Сначала берём кластер (если он задан), иначе конкретный инструмент. При
    малой выборке Beta(2,2) стягивает оценку к 50%, а не позволяет трём
    победам из четырёх называться 75% устойчивым WinRate.
    """
    query = (
        select(
            TradeIdea.p_tp1_before_sl,
            IdeaOutcome.barrier_outcome,
            IdeaOutcome.label_usable,
            TradeIdea.created_at,
        )
        .join(IdeaOutcome, IdeaOutcome.idea_id == TradeIdea.id)
        .order_by(TradeIdea.created_at.desc())
        .limit(240)
    )
    cluster = getattr(instrument, "correlation_cluster", None)
    if cluster:
        query = query.where(TradeIdea.correlation_cluster == cluster)
        source = f"cluster:{cluster}"
    else:
        query = query.where(TradeIdea.instrument_id == instrument.instrument_id)
        source = f"instrument:{instrument.instrument_id}"

    usable: list[tuple[Decimal, int]] = []
    for probability, outcome, label_usable, _created_at in session.execute(query).all():
        if not label_usable:
            continue
        raw = str(outcome)
        if raw == str(BarrierOutcome.WIN):
            label = 1
        elif raw == str(BarrierOutcome.LOSS):
            label = 0
        else:
            continue
        usable.append((_d(probability), label))

    n = len(usable)
    if n == 0:
        return EdgeSnapshot(source=source)
    wins = sum(label for _, label in usable)

    # Beta(2,2) — слабый симметричный prior. Никаких агрессивных Kelly-выводов
    # из нескольких наблюдений.
    posterior = (Decimal(wins) + Decimal(2)) / (Decimal(n) + Decimal(4))
    adequacy = min(Decimal(1), Decimal(n) / Decimal(100))

    brier = sum((p - Decimal(label)) ** 2 for p, label in usable) / Decimal(n)
    # 0.25 — Brier случайной 50/50 оценки; выше неё calibration → 0.
    calibration = _clamp(Decimal(1) - brier / Decimal("0.25"))

    # Стабильность — разница WinRate между свежей и старой половиной. При
    # n<20 признаём, что устойчивость ещё практически не измерена.
    if n < 20:
        stability = Decimal("0.5") * adequacy
    else:
        half = n // 2
        recent = usable[:half]
        older = usable[half:]
        wr_recent = Decimal(sum(v for _, v in recent)) / Decimal(len(recent))
        wr_older = Decimal(sum(v for _, v in older)) / Decimal(len(older))
        stability = _clamp(Decimal(1) - abs(wr_recent - wr_older) / Decimal("0.25"))

    return EdgeSnapshot(
        sample_size=n,
        wins=wins,
        posterior_win=posterior.quantize(Decimal("0.0001")),
        sample_adequacy=adequacy.quantize(Decimal("0.0001")),
        calibration=calibration.quantize(Decimal("0.0001")),
        stability=stability.quantize(Decimal("0.0001")),
        source=source,
    )


def _profile(cfg: EngineConfig, mode: str) -> dict[str, Any]:
    raw = dict(cfg.get(f"risk.management.profiles.{mode}"))
    raw["mode"] = mode
    raw["version"] = str(cfg.get("risk.management.policy_version"))
    return raw


def _mode(edge: EdgeSnapshot, confidence: Decimal) -> str:
    # Малые/нестабильные выборки не блокируются — они получают defensive risk.
    if (
        edge.sample_size < 20
        or confidence < Decimal("0.55")
        or edge.stability < Decimal("0.45")
    ):
        return "defensive"
    if (
        edge.sample_size >= 100
        and confidence >= Decimal("0.70")
        and edge.stability >= Decimal("0.65")
        and edge.posterior_win >= Decimal("0.58")
    ):
        return "conviction"
    return "balanced"


def _remaining_share(trade: PaperTrade) -> Decimal:
    shares = [_d(value) for value in (trade.tp_shares or [])]
    if not shares:
        return Decimal(1)
    taken = int(trade.tps_taken)
    # Для v2 TP3 — activation runner-а: индекс уже пройден, но последняя доля
    # остаётся в позиции. Узнаём v2 по подписанным не-legacy долям.
    dynamic = shares not in (
        [Decimal("0.4"), Decimal("0.4"), Decimal("0.2")],
        [Decimal("0.40"), Decimal("0.40"), Decimal("0.20")],
    )
    if dynamic and taken >= len(shares) and len(shares) >= 3:
        return shares[-1]
    return max(Decimal(0), Decimal(1) - sum(shares[:taken], Decimal(0)))


def _open_risk(session: Session, *, risk_equity: Decimal, cluster: str | None) -> tuple[Decimal, Decimal]:
    """Текущий downside по current_stop, а не исходный зарезервированный риск.

    После переноса stop к entry или в прибыль риск освобождается и может быть
    использован другой независимой сделкой. Максимальные лимиты при этом не
    меняются ни на копейку.
    """
    if risk_equity <= 0:
        return Decimal(0), Decimal(0)
    rows = session.execute(
        select(PaperTrade, TradeIdea)
        .join(TradeIdea, TradeIdea.id == PaperTrade.idea_id)
        .where(PaperTrade.status.in_([PaperStatus.PENDING, PaperStatus.OPEN]))
    ).all()
    total = Decimal(0)
    in_cluster = Decimal(0)
    for trade, idea in rows:
        planned = _d(idea.risk_amount or 0)
        if planned <= 0:
            continue
        if PaperStatus(trade.status) is PaperStatus.PENDING:
            current = planned
        else:
            initial = abs(_d(trade.entry) - _d(trade.initial_stop))
            if initial <= 0:
                continue
            long = str(trade.direction).endswith("LONG")
            downside = (
                max(Decimal(0), (_d(trade.entry) - _d(trade.current_stop)) / initial)
                if long
                else max(Decimal(0), (_d(trade.current_stop) - _d(trade.entry)) / initial)
            )
            current = planned * _remaining_share(trade) * downside
        pct = current / risk_equity
        total += pct
        if cluster and idea.correlation_cluster == cluster:
            in_cluster += pct
    return total, in_cluster


def _confidence_v2(inputs: ConfidenceInput):
    ctx = _CTX.get()
    if ctx is None:
        return _ORIGINAL_CONFIDENCE(inputs)
    enhanced = replace(
        inputs,
        sample_adequacy=ctx.edge.sample_adequacy,
        calibration=ctx.edge.calibration,
        stability=ctx.edge.stability,
    )
    result = _ORIGINAL_CONFIDENCE(enhanced)
    ctx.confidence = result[0]
    return result


def _probability_v2(score: Decimal, *, cap: Decimal = Decimal("0.68"), floor: Decimal = Decimal("0.35")) -> ProbabilityEstimate:
    base = _ORIGINAL_RULE_PRIOR(score, cap=cap, floor=floor)
    ctx = _CTX.get()
    if ctx is None or ctx.edge.sample_size <= 0:
        return base
    return empirical(
        ctx.edge.wins,
        ctx.edge.sample_size,
        prior=base.p_tp1_before_sl,
        shrinkage_k=int(ctx.cfg.get("probability.shrinkage_k")),
        source=ctx.edge.source,
        min_sample=int(ctx.cfg.get("probability.min_oos_trades_for_model")),
        cap=cap,
    )


def _expected_value_v2(*, p_win: Decimal, avg_win_r: Decimal, avg_loss_r: Decimal, p_timeout: Decimal = Decimal(0), timeout_cost_r: Decimal = Decimal(0), costs_r: Decimal = Decimal(0)) -> Decimal:
    """P(TP1 before SL) больше не умножается на R/R до TP2.

    До появления полноценного multi-barrier distribution используем
    консервативный 1R payoff для события TP1. Это семантически совпадающие
    величины и не завышает admission.
    """
    compatible_win = min(abs(avg_win_r), Decimal(1))
    return _ORIGINAL_EXPECTED_VALUE(
        p_win=p_win,
        avg_win_r=compatible_win,
        avg_loss_r=avg_loss_r,
        p_timeout=p_timeout,
        timeout_cost_r=timeout_cost_r,
        costs_r=costs_r,
    )


def _budget_v2(*, score: Decimal, state: RiskState, limits=None, strategy_multiplier: Decimal = Decimal(1)):
    ctx = _CTX.get()
    if ctx is None:
        return _ORIGINAL_COMPUTE_BUDGET(
            score=score,
            state=state,
            limits=limits,
            strategy_multiplier=strategy_multiplier,
        )

    mode = _mode(ctx.edge, ctx.confidence)
    profile = _profile(ctx.cfg, mode)
    ctx.mode = mode
    ctx.profile = profile

    open_pct, cluster_pct = _open_risk(
        ctx.session,
        risk_equity=state.risk_equity,
        cluster=getattr(ctx.instrument, "correlation_cluster", None),
    )
    effective_state = replace(
        state,
        open_risk_pct=max(state.open_risk_pct, open_pct),
        cluster_risk_pct=max(state.cluster_risk_pct, cluster_pct),
    )
    # Profile multiplier может только урезать. Верхняя граница 1 защищает от
    # случайной/LLM-сгенерированной попытки увеличить абсолютный риск.
    evidence_multiplier = min(Decimal(1), _d(profile["risk_multiplier"]))
    return _ORIGINAL_COMPUTE_BUDGET(
        score=score,
        state=effective_state,
        limits=limits,
        strategy_multiplier=min(Decimal(1), strategy_multiplier * evidence_multiplier),
    )


def _scan_instrument_v2(session: Session, instrument: Any, *, cfg: EngineConfig, risk_state: RiskState, now):
    ctx = RuntimeContext(
        session=session,
        instrument=instrument,
        cfg=cfg,
        edge=_edge_snapshot(session, instrument),
    )
    token = _CTX.set(ctx)
    try:
        idea, skipped, rejections = _ORIGINAL_SCAN_INSTRUMENT(
            session, instrument, cfg=cfg, risk_state=risk_state, now=now
        )
        if idea is not None:
            profile = ctx.profile or _profile(cfg, _mode(ctx.edge, ctx.confidence))
            explanation = dict(idea.explanation_json or {})
            explanation["risk_policy"] = {
                "version": profile["version"],
                "mode": profile["mode"],
                "risk_multiplier": str(profile["risk_multiplier"]),
                "tp_shares": [str(v) for v in profile["tp_shares"]],
                "runner_enabled": bool(profile.get("runner_enabled", True)),
                "runner_activation_r": str(profile["runner_activation_r"]),
                "atr_multiplier": str(profile["atr_multiplier"]),
                "min_trail_r": str(profile["min_trail_r"]),
                "mfe_giveback": str(profile["mfe_giveback"]),
                "tp1_stop_lock_r": str(profile["tp1_stop_lock_r"]),
                "tp2_stop_lock_r": str(profile["tp2_stop_lock_r"]),
                "time_stop_hours": int(profile["time_stop_hours"]),
            }
            explanation["risk_edge"] = {
                "source": ctx.edge.source,
                "sample_size": ctx.edge.sample_size,
                "posterior_win": str(ctx.edge.posterior_win),
                "sample_adequacy": str(ctx.edge.sample_adequacy),
                "calibration": None if ctx.edge.calibration is None else str(ctx.edge.calibration),
                "stability": str(ctx.edge.stability),
                "confidence": str(ctx.confidence),
            }
            idea.explanation_json = explanation
        return idea, skipped, rejections
    finally:
        _CTX.reset(token)


def _track_v2(session: Session, *, now=None):
    report = _ORIGINAL_TRACK(session, now=now)
    # Оптимизатор запускается в том же последовательном scheduler-процессе,
    # а не отдельным daemon-ом. Он сам проверяет weekly cadence.
    from .optimizer import maybe_optimize

    note = maybe_optimize(session, now=now)
    if note:
        report.details.append(note)
    return report


def install() -> None:
    """Установить ровно один adaptive risk/exit runtime в scheduler."""
    global _INSTALLED
    global _ORIGINAL_SCAN_INSTRUMENT, _ORIGINAL_COMPUTE_BUDGET
    global _ORIGINAL_CONFIDENCE, _ORIGINAL_RULE_PRIOR, _ORIGINAL_EXPECTED_VALUE
    global _ORIGINAL_TRACK
    if _INSTALLED:
        assert_single_runtime()
        return

    from ..paper import live_tracker, tracker
    from ..pipeline import scan as scan_module
    from .dynamic_exit import advance_v2

    _ORIGINAL_SCAN_INSTRUMENT = scan_module.scan_instrument
    _ORIGINAL_COMPUTE_BUDGET = scan_module.compute_budget
    _ORIGINAL_CONFIDENCE = scan_module.confidence
    _ORIGINAL_RULE_PRIOR = scan_module.rule_prior
    _ORIGINAL_EXPECTED_VALUE = scan_module.expected_value
    _ORIGINAL_TRACK = tracker.track

    scan_module.scan_instrument = _scan_instrument_v2
    scan_module.compute_budget = _budget_v2
    scan_module.confidence = _confidence_v2
    scan_module.rule_prior = _probability_v2
    scan_module.expected_value = _expected_value_v2

    # Оба execution sensor-а используют ОДНУ и ту же функцию сопровождения.
    tracker.advance = advance_v2
    live_tracker.advance = advance_v2
    tracker.track = _track_v2

    _INSTALLED = True
    assert_single_runtime()


def assert_single_runtime() -> None:
    """Fail-fast: второй активный риск/exit путь запрещён."""
    if not _INSTALLED:
        raise RuntimeError("Risk Engine v2 не установлен")
    from ..paper import live_tracker, tracker
    from ..pipeline import scan as scan_module
    from .dynamic_exit import advance_v2

    checks = {
        "scan_instrument": scan_module.scan_instrument is _scan_instrument_v2,
        "compute_budget": scan_module.compute_budget is _budget_v2,
        "confidence": scan_module.confidence is _confidence_v2,
        "probability": scan_module.rule_prior is _probability_v2,
        "expected_value": scan_module.expected_value is _expected_value_v2,
        "paper_exit": tracker.advance is advance_v2,
        "crypto_exit": live_tracker.advance is advance_v2,
        "optimizer_hook": tracker.track is _track_v2,
    }
    broken = [name for name, ok in checks.items() if not ok]
    if broken:
        raise RuntimeError(
            "обнаружен параллельный/подменённый risk runtime: " + ", ".join(broken)
        )


def runtime_report() -> dict[str, Any]:
    assert_single_runtime()
    return {
        "installed": True,
        "risk_calculators": 1,
        "exit_engines": 1,
        "paper_and_crypto_share_exit_engine": True,
        "absolute_risk_caps_changed": False,
    }


__all__ = ["EdgeSnapshot", "assert_single_runtime", "install", "runtime_report"]
