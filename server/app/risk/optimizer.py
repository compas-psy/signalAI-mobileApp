"""Guarded champion/challenger optimizer for Risk & Exit Engine v2.

Числовой optimizer ищет policy только в заранее ограниченном наборе
кандидатов из config. Он не может изменить абсолютные risk caps. Локальная
LLM получает уже рассчитанные OOS-метрики и выступает только критиком
переобучения/нестабильности; чисел не считает и торговых команд не выдаёт.

Promotion применяется только к НОВЫМ идеям. Каждая идея сохраняет полный
RiskPolicy snapshot, а PaperTrade — уникальные доли policy, по которым exit
engine восстанавливает подписанный профиль после рестарта.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..backtest.bybit_dataset import DATA_READY
from ..config import EngineConfig, get_config
from ..models import (
    BacktestRun,
    Bar,
    DatasetSnapshot,
    IdeaOutcome,
    Instrument,
    ModelRegistry,
    PaperTrade,
    TradeIdea,
)
from ..models.enums import PaperStatus, Timeframe
from ..research import gateway
from ..version import ENGINE_VERSION

LABEL = "risk-exit-v2"
MODEL_NAME = "risk_exit_policy"


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


def _candidate(cfg: EngineConfig, candidate_id: str) -> dict[str, Any] | None:
    for raw in cfg.get("risk.management.optimizer.candidates"):
        if str(raw.get("id")) == candidate_id:
            return dict(raw)
    return None


def champion_profile_override(session: Session, mode: str, *, cfg: EngineConfig | None = None) -> dict[str, Any] | None:
    """Overlay действующего champion-а для новой идеи."""
    config = cfg or get_config()
    champion = session.execute(
        select(ModelRegistry)
        .where(ModelRegistry.name == MODEL_NAME, ModelRegistry.role == "champion")
        .order_by(ModelRegistry.promoted_at.desc().nullslast(), ModelRegistry.created_at.desc())
        .limit(1)
    ).scalars().first()
    if champion is None:
        return None
    candidate_id = str((champion.calibration_json or {}).get("candidate_id", ""))
    candidate = _candidate(config, candidate_id)
    if not candidate:
        return None
    profile = (candidate.get("profiles") or {}).get(mode)
    return dict(profile) if isinstance(profile, dict) else None


def _enrich_closed_outcomes(session: Session) -> int:
    """Заполнить фактический R/MFE/MAE по уже закрытым server-paper сделкам."""
    rows = session.execute(
        select(IdeaOutcome, PaperTrade, TradeIdea)
        .join(PaperTrade, PaperTrade.idea_id == IdeaOutcome.idea_id)
        .join(TradeIdea, TradeIdea.id == IdeaOutcome.idea_id)
        .where(PaperTrade.status == PaperStatus.CLOSED)
        .order_by(PaperTrade.closed_at.desc())
        .limit(400)
    ).all()
    changed = 0
    for outcome, trade, idea in rows:
        if outcome.actual_r is not None and outcome.mfe_r is not None and outcome.mae_r is not None:
            continue
        if trade.closed_at is None:
            continue
        start = trade.opened_at
        end = trade.closed_at
        bars = list(
            session.execute(
                select(Bar)
                .where(
                    Bar.instrument_id == trade.instrument_id,
                    Bar.timeframe == Timeframe.H1,
                    Bar.is_closed.is_(True),
                    Bar.open_time >= start,
                    Bar.open_time <= end,
                )
                .order_by(Bar.open_time)
            ).scalars()
        )
        risk = abs(_d(trade.entry) - _d(trade.initial_stop))
        if risk <= 0:
            continue
        long = str(trade.direction).endswith("LONG")
        mfe = Decimal(0)
        mae = Decimal(0)
        for bar in bars:
            favorable = (
                (_d(bar.high) - _d(trade.entry)) / risk
                if long else (_d(trade.entry) - _d(bar.low)) / risk
            )
            adverse = (
                (_d(trade.entry) - _d(bar.low)) / risk
                if long else (_d(bar.high) - _d(trade.entry)) / risk
            )
            mfe = max(mfe, favorable)
            mae = max(mae, adverse)
        outcome.actual_r = _d(trade.realized_r)
        outcome.mfe_r = max(Decimal(0), mfe)
        outcome.mae_r = max(Decimal(0), mae)
        detail = dict(outcome.detail_json or {})
        detail["risk_policy"] = (idea.explanation_json or {}).get("risk_policy", {})
        detail["paper_trade_id"] = str(trade.id)
        outcome.detail_json = detail
        changed += 1
    return changed


def _policy_rows(session: Session, limit: int = 400) -> list[dict[str, Any]]:
    rows = session.execute(
        select(IdeaOutcome, TradeIdea, Instrument)
        .join(TradeIdea, TradeIdea.id == IdeaOutcome.idea_id)
        .join(Instrument, Instrument.instrument_id == TradeIdea.instrument_id)
        .where(
            IdeaOutcome.label_usable.is_(True),
            IdeaOutcome.actual_r.is_not(None),
            IdeaOutcome.mfe_r.is_not(None),
        )
        .order_by(TradeIdea.created_at.desc())
        .limit(limit)
    ).all()
    result: list[dict[str, Any]] = []
    for outcome, idea, instrument in reversed(rows):
        policy = (idea.explanation_json or {}).get("risk_policy") or {}
        mode = str(policy.get("mode", ""))
        if mode not in {"defensive", "balanced", "conviction"}:
            continue
        result.append(
            {
                "at": idea.created_at,
                "mode": mode,
                "actual": _d(outcome.actual_r),
                "mfe": _d(outcome.mfe_r),
                "instrument_id": idea.instrument_id,
                "venue": instrument.venue.value,
                "symbol": instrument.symbol,
            }
        )
    return result


def _dataset_readiness_blockers(
    session: Session,
    rows: list[dict[str, Any]],
) -> tuple[str, ...]:
    """Return per-symbol blockers only for crypto evidence in this optimizer sample.

    FORTS evidence is independent from Bybit history. Crypto rows, however,
    cannot participate in optimization until the exact symbol has a latest
    persisted 36-month multi-stream snapshot marked ``DATA_READY``.
    """
    crypto_symbols = sorted(
        {
            str(row.get("symbol", "")).strip()
            for row in rows
            if str(row.get("venue", "")) == "CRYPTO"
            and str(row.get("symbol", "")).strip()
        }
    )
    blockers: list[str] = []
    for symbol in crypto_symbols:
        snapshot = session.execute(
            select(DatasetSnapshot)
            .where(DatasetSnapshot.dataset_name == f"bybit:{symbol}:multistream")
            .order_by(
                DatasetSnapshot.tradable_at.desc(),
                DatasetSnapshot.created_at.desc(),
            )
            .limit(1)
        ).scalars().first()
        if snapshot is None:
            blockers.append(f"DATASET_MISSING:{symbol}")
            continue
        readiness = str((snapshot.source_watermark or {}).get("readiness", ""))
        if readiness != DATA_READY:
            blockers.append(f"DATA_BLOCKED:{symbol}")
    return tuple(blockers)


def _simulate(row: dict[str, Any], profile: dict[str, Any]) -> Decimal:
    """Консервативная MFE-based оценка exit policy.

    Это не реконструкция тиков и не используется для исхода сделки. Нужна
    только для сравнительного champion/challenger отбора на одинаковых
    наблюдениях. Если MFE не достиг TP1, сохраняется фактический outcome.
    """
    actual, mfe = row["actual"], max(Decimal(0), row["mfe"])
    shares = [_d(v) for v in profile["tp_shares"]]
    total = sum(shares, Decimal(0))
    shares = [v / total for v in shares]
    if mfe < Decimal(1):
        return actual

    value = shares[0] * Decimal(1)
    remaining = Decimal(1) - shares[0]
    if mfe < Decimal(2):
        return value + remaining * actual

    value += shares[1] * Decimal(2)
    remaining -= shares[1]
    activation = _d(profile["runner_activation_r"])
    if mfe < activation:
        return value + remaining * actual

    # Runner lock — минимум между MFE giveback и volatility floor не можем
    # реконструировать без полного intrabar path. Берём более консервативный
    # giveback-only floor и никогда не выше MFE.
    runner_exit = max(
        _d(profile["tp2_stop_lock_r"]),
        mfe * (Decimal(1) - _d(profile["mfe_giveback"])),
    )
    runner_exit = min(mfe, runner_exit)
    return value + remaining * runner_exit


def _metrics(values: list[Decimal]) -> dict[str, Decimal]:
    if not values:
        return {"expectancy": Decimal(0), "max_drawdown": Decimal(0), "top5": Decimal(1)}
    expectancy = sum(values, Decimal(0)) / Decimal(len(values))
    equity = Decimal(0)
    peak = Decimal(0)
    max_dd = Decimal(0)
    positives: list[Decimal] = []
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if value > 0:
            positives.append(value)
    total_positive = sum(positives, Decimal(0))
    top5 = (
        sum(sorted(positives, reverse=True)[:5], Decimal(0)) / total_positive
        if total_positive > 0 else Decimal(1)
    )
    return {
        "expectancy": expectancy.quantize(Decimal("0.0001")),
        "max_drawdown": max_dd.quantize(Decimal("0.0001")),
        "top5": top5.quantize(Decimal("0.0001")),
    }


def _evaluate(rows: list[dict[str, Any]], candidate: dict[str, Any]) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    cut = max(1, int(len(rows) * 0.70))
    train, test = rows[:cut], rows[cut:]

    def run(part):
        return [
            _simulate(row, candidate["profiles"][row["mode"]])
            for row in part
        ]

    return _metrics(run(train)), _metrics(run(test))


def _llm_review(*, candidate_id: str, baseline: dict[str, Decimal], challenger: dict[str, Decimal], sample_size: int) -> tuple[bool, dict[str, Any]]:
    schema = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "reject", "needs_more_data"]},
            "fragility": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
        "required": ["verdict", "fragility", "summary"],
        "additionalProperties": False,
    }
    document = json.dumps(
        {
            "candidate_id": candidate_id,
            "sample_size": sample_size,
            "baseline_oos": {k: str(v) for k, v in baseline.items()},
            "challenger_oos": {k: str(v) for k, v in challenger.items()},
        },
        ensure_ascii=False,
    )
    try:
        payload, exchange = gateway.ask(
            system=(
                "Ты независимый критик уже рассчитанного risk-policy эксперимента. "
                "Не рассчитывай новые числа, не предлагай торговые действия и не "
                "меняй параметры. Проверь только: малую выборку, концентрацию "
                "результата, ухудшение drawdown и очевидную нестабильность."
            ),
            document=document,
            schema=schema,
            forbidden=("buy", "sell", "покупать", "продавать"),
            attempts=2,
        )
        payload["attempts"] = exchange.attempts
        return payload.get("verdict") == "pass", payload
    except gateway.GatewayUnavailable as exc:
        return False, {"verdict": "unavailable", "summary": str(exc), "fragility": []}


def _universe(rows: list[dict[str, Any]]) -> list[str]:
    values = {
        "CRYPTO" if str(row.get("venue", "")) == "CRYPTO" else "FORTS"
        for row in rows
        if str(row.get("venue", ""))
    }
    return sorted(values) if values else ["FORTS", "CRYPTO"]


def _record_run(session: Session, *, cfg: EngineConfig, rows: list[dict[str, Any]], candidate_id: str, metrics: dict[str, Decimal], gate: bool, detail: dict[str, Any]) -> BacktestRun:
    dates = [row["at"].date() for row in rows] or [date.today()]
    run = BacktestRun(
        label=f"{LABEL}:{candidate_id}",
        strategy=None,
        period_from=min(dates),
        period_to=max(dates),
        config_hash=cfg.config_hash,
        engine_version=ENGINE_VERSION,
        universe_json=_universe(rows),
        trades=len(rows),
        net_return=sum((_d(row["actual"]) for row in rows), Decimal(0)),
        profit_factor=None,
        expectancy_r=metrics["expectancy"],
        max_drawdown=metrics["max_drawdown"],
        sharpe=None,
        sortino=None,
        calmar=None,
        brier_score=None,
        pbo=None,
        top5_contribution=metrics["top5"],
        report_json=detail,
        gate_passed=gate,
        gate_detail_json=detail,
    )
    session.add(run)
    return run


def _promote(session: Session, *, cfg: EngineConfig, candidate_id: str, rows: list[dict[str, Any]], metrics: dict[str, Decimal], review: dict[str, Any]) -> ModelRegistry:
    current = session.execute(
        select(ModelRegistry).where(ModelRegistry.name == MODEL_NAME, ModelRegistry.role == "champion")
    ).scalars().all()
    for model in current:
        model.role = "retired"

    dates = [row["at"].date() for row in rows]
    model = ModelRegistry(
        name=MODEL_NAME,
        version=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        role="champion",
        algorithm="bounded_walk_forward_llm_critic_v2",
        trained_from=min(dates),
        trained_to=max(dates),
        sample_size=len(rows),
        oos_brier=None,
        oos_ece=None,
        calibration_json={
            "candidate_id": candidate_id,
            "metrics": {k: str(v) for k, v in metrics.items()},
            "llm_review": review,
            "owner_preapproved_bounded_auto_promotion": True,
            "absolute_risk_caps_changed": False,
        },
        feature_list=["actual_r", "mfe_r", "risk_policy.mode"],
        # Владелец заранее разрешил автоматическую bounded-оптимизацию, но это
        # не ручное утверждение конкретной версии — поле остаётся честным.
        approved_by_human=False,
        promoted_at=datetime.now(UTC),
    )
    session.add(model)
    return model


def maybe_optimize(session: Session, *, now: datetime | None = None) -> str:
    """Weekly optimizer hook. Быстрый no-op во все остальные paper ticks."""
    moment = now or datetime.now(UTC)
    cfg = get_config()
    cadence_days = int(cfg.get("risk.management.optimizer.cadence_days"))
    latest = session.execute(
        select(func.max(BacktestRun.created_at)).where(BacktestRun.label.like(f"{LABEL}:%"))
    ).scalar_one_or_none()
    if latest is not None:
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        if moment - latest < timedelta(days=cadence_days):
            return ""

    enriched = _enrich_closed_outcomes(session)
    rows = _policy_rows(session)
    minimum = int(cfg.get("risk.management.optimizer.min_samples"))
    if len(rows) < minimum:
        metrics = _metrics([row["actual"] for row in rows])
        _record_run(
            session,
            cfg=cfg,
            rows=rows,
            candidate_id="insufficient_data",
            metrics=metrics,
            gate=False,
            detail={"reason": f"нужно {minimum}, доступно {len(rows)}", "enriched": enriched},
        )
        return f"risk optimizer: данных {len(rows)}/{minimum}, promotion выключен"

    dataset_blockers = _dataset_readiness_blockers(session, rows)
    if dataset_blockers:
        metrics = _metrics([row["actual"] for row in rows])
        detail = {
            "reason": "DATASET_NOT_READY",
            "dataset_blockers": list(dataset_blockers),
            "enriched": enriched,
        }
        _record_run(
            session,
            cfg=cfg,
            rows=rows,
            candidate_id="dataset_blocked",
            metrics=metrics,
            gate=False,
            detail=detail,
        )
        return (
            "risk optimizer: dataset gate blocked ("
            + ", ".join(dataset_blockers)
            + "), promotion выключен"
        )

    candidates = [dict(item) for item in cfg.get("risk.management.optimizer.candidates")]
    baseline = next(item for item in candidates if item.get("id") == "baseline")
    _, baseline_oos = _evaluate(rows, baseline)

    evaluated = []
    for candidate in candidates:
        train, test = _evaluate(rows, candidate)
        # Выбор делается по train utility, OOS используется только как gate.
        utility = train["expectancy"] - train["max_drawdown"] * Decimal("0.05") - train["top5"] * Decimal("0.10")
        evaluated.append((utility, candidate, train, test))
    evaluated.sort(key=lambda item: item[0], reverse=True)
    _, winner, train_metrics, test_metrics = evaluated[0]

    min_improvement = _d(cfg.get("risk.management.optimizer.min_oos_expectancy_improvement_r"))
    max_top5 = _d(cfg.get("backtest.paper_gate.max_top5_contribution"))
    quantitative_gate = (
        test_metrics["expectancy"] >= baseline_oos["expectancy"] + min_improvement
        and test_metrics["top5"] <= max_top5
        and test_metrics["max_drawdown"] <= baseline_oos["max_drawdown"] * Decimal("1.10") + Decimal("0.25")
    )
    llm_pass, review = _llm_review(
        candidate_id=str(winner["id"]),
        baseline=baseline_oos,
        challenger=test_metrics,
        sample_size=len(rows),
    )
    gate = quantitative_gate and llm_pass
    detail = {
        "winner": winner["id"],
        "train": {k: str(v) for k, v in train_metrics.items()},
        "oos": {k: str(v) for k, v in test_metrics.items()},
        "baseline_oos": {k: str(v) for k, v in baseline_oos.items()},
        "quantitative_gate": quantitative_gate,
        "llm_review": review,
        "enriched": enriched,
    }
    _record_run(
        session,
        cfg=cfg,
        rows=rows,
        candidate_id=str(winner["id"]),
        metrics=test_metrics,
        gate=gate,
        detail=detail,
    )
    if gate:
        _promote(
            session,
            cfg=cfg,
            candidate_id=str(winner["id"]),
            rows=rows,
            metrics=test_metrics,
            review=review,
        )
        return f"risk optimizer: champion → {winner['id']} (OOS {test_metrics['expectancy']}R)"
    return f"risk optimizer: challenger {winner['id']} не прошёл promotion gate"


__all__ = ["champion_profile_override", "maybe_optimize"]
