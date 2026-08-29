"""Read-only owner control-plane snapshot.

Control separates three different truths that must never be conflated:
1. the legacy production scanner that can publish owner-facing TradeIdea rows;
2. Shadow/Paper champion-challenger measurement;
3. immutable historical dataset/backtest readiness.

The endpoint only aggregates persisted evidence. It does not scan, replay,
promote strategies or mutate risk state.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, aliased

from ..config import EngineConfig, get_config
from ..models import (
    BacktestRun,
    DatasetSnapshot,
    Instrument,
    ModelRegistry,
    PaperAbDecision,
    PaperAbOutcome,
    ShadowObservation,
    TradeIdea,
)
from ..models.enums import AssetClass, Venue
from .bybit_funnel import dashboard_funnel
from .runtime_roles import compose_runtime_roles, registry_role_map

ExternalVenue = Literal["FORTS", "BYBIT"]
_CONTROL_VERSION = "legacy_control_v1"
_RISK_MODEL_NAME = "risk_exit_policy"
_RISK_RUN_PREFIX = "risk-exit-v2:%"


def _number(value: Decimal | int | float | None) -> float | None:
    return None if value is None else float(value)


def _enum(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw)


def _venue_spec(venue: ExternalVenue) -> tuple[set[str], Venue, AssetClass, str]:
    if venue == "FORTS":
        return {"MOEX", "FORTS"}, Venue.MOEX, AssetClass.FUTURES, "FORTS"
    if venue == "BYBIT":
        return {"CRYPTO", "BYBIT"}, Venue.CRYPTO, AssetClass.CRYPTO_PERPETUAL, "CRYPTO"
    raise ValueError(f"unsupported control-plane venue: {venue}")


def _control_funnel(
    session: Session,
    *,
    start: datetime,
    venue_enum: Venue,
    asset_class: AssetClass,
) -> dict[str, Any]:
    filters = (
        TradeIdea.signal_time >= start,
        Instrument.venue == venue_enum,
        Instrument.asset_class == asset_class,
    )
    total, presented = session.execute(
        select(
            func.count(TradeIdea.id),
            func.count(TradeIdea.id).filter(TradeIdea.was_presented.is_(True)),
        )
        .join(Instrument, Instrument.instrument_id == TradeIdea.instrument_id)
        .where(*filters)
    ).one()
    statuses = {
        _enum(name): int(count)
        for name, count in session.execute(
            select(TradeIdea.status, func.count(TradeIdea.id))
            .join(Instrument, Instrument.instrument_id == TradeIdea.instrument_id)
            .where(*filters)
            .group_by(TradeIdea.status)
            .order_by(TradeIdea.status)
        )
    }
    qualities = {
        _enum(name): int(count)
        for name, count in session.execute(
            select(TradeIdea.quality_status, func.count(TradeIdea.id))
            .join(Instrument, Instrument.instrument_id == TradeIdea.instrument_id)
            .where(*filters)
            .group_by(TradeIdea.quality_status)
            .order_by(TradeIdea.quality_status)
        )
    }
    versions = {
        str(name or "unknown"): int(count)
        for name, count in session.execute(
            select(TradeIdea.strategy_version, func.count(TradeIdea.id))
            .join(Instrument, Instrument.instrument_id == TradeIdea.instrument_id)
            .where(*filters)
            .group_by(TradeIdea.strategy_version)
            .order_by(TradeIdea.strategy_version)
        )
    }
    return {
        "ideas_created": int(total or 0),
        "presented": int(presented or 0),
        "statuses": statuses,
        "qualities": qualities,
        "versions": versions,
    }


def _shadow_rows(
    session: Session,
    *,
    start: datetime,
    venue_aliases: set[str],
) -> list[dict[str, Any]]:
    grouped = session.execute(
        select(
            ShadowObservation.strategy_version,
            ShadowObservation.evidence_status,
            ShadowObservation.reason_code,
            ShadowObservation.signal_emitted,
            func.count(ShadowObservation.id),
        )
        .where(
            ShadowObservation.evaluated_at >= start,
            ShadowObservation.venue.in_(sorted(venue_aliases)),
        )
        .group_by(
            ShadowObservation.strategy_version,
            ShadowObservation.evidence_status,
            ShadowObservation.reason_code,
            ShadowObservation.signal_emitted,
        )
    ).all()
    by_version: dict[str, dict[str, Any]] = {}
    for version, evidence, reason, emitted, count in grouped:
        row = by_version.setdefault(
            str(version),
            {
                "version": str(version),
                "observations": 0,
                "evaluated": 0,
                "unavailable": 0,
                "emitted": 0,
                "_reasons": defaultdict(int),
            },
        )
        n = int(count)
        row["observations"] += n
        if str(evidence) == "EVALUATED":
            row["evaluated"] += n
        else:
            row["unavailable"] += n
            if reason:
                row["_reasons"][str(reason)] += n
        if bool(emitted):
            row["emitted"] += n

    output: list[dict[str, Any]] = []
    for version in sorted(by_version):
        row = by_version[version]
        reasons: dict[str, int] = row.pop("_reasons")
        if (
            row["observations"] > 0
            and row["unavailable"] == row["observations"]
            and set(reasons) == {"INSTRUMENT_SCOPE_UNSUPPORTED"}
        ):
            continue
        row["top_unavailable_reasons"] = [
            {"reason": reason, "count": count}
            for reason, count in sorted(
                reasons.items(), key=lambda item: (-item[1], item[0])
            )[:5]
        ]
        output.append(row)
    return output


def _paper_stats(
    session: Session,
    *,
    start: datetime,
    venue_aliases: set[str],
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, Any]], str]:
    grouped = session.execute(
        select(
            PaperAbDecision.candidate_version,
            PaperAbDecision.arm_role,
            func.count(PaperAbDecision.id),
            func.count(PaperAbDecision.id).filter(PaperAbDecision.signal_emitted.is_(True)),
            func.count(PaperAbOutcome.id),
            func.count(PaperAbOutcome.id).filter(PaperAbOutcome.evidence_status == "EVALUATED"),
            func.avg(PaperAbOutcome.net_r).filter(
                PaperAbOutcome.evidence_status == "EVALUATED",
                PaperAbOutcome.net_r.is_not(None),
            ),
        )
        .outerjoin(PaperAbOutcome, PaperAbOutcome.decision_id == PaperAbDecision.id)
        .where(
            PaperAbDecision.decision_at >= start,
            PaperAbDecision.venue.in_(sorted(venue_aliases)),
        )
        .group_by(PaperAbDecision.candidate_version, PaperAbDecision.arm_role)
    ).all()

    stats: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for version, role, decisions, emitted, outcomes, evaluated, mean_r in grouped:
        n_decisions = int(decisions or 0)
        n_outcomes = int(outcomes or 0)
        stats[str(version)][str(role)] = {
            "decisions": n_decisions,
            "emitted": int(emitted or 0),
            "evaluated_outcomes": int(evaluated or 0),
            "pending_outcomes": max(0, n_decisions - n_outcomes),
            "unavailable_outcomes": max(0, n_outcomes - int(evaluated or 0)),
            "mean_net_r": _number(mean_r),
        }

    control_d = aliased(PaperAbDecision)
    candidate_d = aliased(PaperAbDecision)
    control_o = aliased(PaperAbOutcome)
    candidate_o = aliased(PaperAbOutcome)
    paired_rows = session.execute(
        select(
            candidate_d.candidate_version,
            func.count(func.distinct(control_d.pair_key)),
            func.avg(control_o.net_r),
            func.avg(candidate_o.net_r),
        )
        .select_from(control_d)
        .join(
            candidate_d,
            and_(
                candidate_d.pair_key == control_d.pair_key,
                candidate_d.arm_role == "CANDIDATE",
            ),
        )
        .join(control_o, control_o.decision_id == control_d.id)
        .join(candidate_o, candidate_o.decision_id == candidate_d.id)
        .where(
            control_d.arm_role == "CONTROL",
            control_d.decision_at >= start,
            candidate_d.decision_at >= start,
            control_d.venue.in_(sorted(venue_aliases)),
            candidate_d.venue.in_(sorted(venue_aliases)),
            control_o.evidence_status == "EVALUATED",
            candidate_o.evidence_status == "EVALUATED",
            control_o.net_r.is_not(None),
            candidate_o.net_r.is_not(None),
        )
        .group_by(candidate_d.candidate_version)
    ).all()
    paired = {
        str(version): {
            "comparable_pairs": int(count or 0),
            "control_mean_net_r": _number(control_mean),
            "candidate_mean_net_r": _number(candidate_mean),
            "delta_mean_net_r": (
                None
                if control_mean is None or candidate_mean is None
                else float(candidate_mean - control_mean)
            ),
        }
        for version, count, control_mean, candidate_mean in paired_rows
    }
    control_versions = session.execute(
        select(PaperAbDecision.strategy_version, func.count(PaperAbDecision.id))
        .where(
            PaperAbDecision.decision_at >= start,
            PaperAbDecision.venue.in_(sorted(venue_aliases)),
            PaperAbDecision.arm_role == "CONTROL",
        )
        .group_by(PaperAbDecision.strategy_version)
        .order_by(func.count(PaperAbDecision.id).desc(), PaperAbDecision.strategy_version)
        .limit(1)
    ).first()
    control_version = str(control_versions[0]) if control_versions else _CONTROL_VERSION
    return stats, paired, control_version


def _empty_arm() -> dict[str, Any]:
    return {
        "decisions": 0,
        "emitted": 0,
        "evaluated_outcomes": 0,
        "pending_outcomes": 0,
        "unavailable_outcomes": 0,
        "mean_net_r": None,
    }


def _competition(
    *,
    shadow: list[dict[str, Any]],
    paper: dict[str, dict[str, dict[str, Any]]],
    paired: dict[str, dict[str, Any]],
    control_version: str,
    min_sample: int,
) -> dict[str, Any]:
    shadow_by_version = {row["version"]: row for row in shadow}
    versions = sorted(set(shadow_by_version) | set(paper))
    rows: list[dict[str, Any]] = []
    for version in versions:
        shadow_row = shadow_by_version.get(
            version,
            {
                "version": version,
                "observations": 0,
                "evaluated": 0,
                "unavailable": 0,
                "emitted": 0,
                "top_unavailable_reasons": [],
            },
        )
        arms = paper.get(version, {})
        control_arm = dict(arms.get("CONTROL") or _empty_arm())
        candidate_arm = dict(arms.get("CANDIDATE") or _empty_arm())
        paired_row = dict(
            paired.get(
                version,
                {
                    "comparable_pairs": 0,
                    "control_mean_net_r": None,
                    "candidate_mean_net_r": None,
                    "delta_mean_net_r": None,
                },
            )
        )
        observations = int(shadow_row["observations"])
        unavailable = int(shadow_row["unavailable"])
        evaluated = int(shadow_row["evaluated"])
        broken_input = (
            observations > 0
            and evaluated == 0
            and unavailable == observations
            and any(
                item["reason"] != "INSTRUMENT_SCOPE_UNSUPPORTED"
                for item in shadow_row["top_unavailable_reasons"]
            )
        )
        comparable = int(paired_row["comparable_pairs"])
        sample_adequate = comparable >= min_sample
        paired_row["required_pairs"] = min_sample
        paired_row["remaining_pairs"] = max(0, min_sample - comparable)
        paired_row["sample_adequate"] = sample_adequate
        paper_decisions = int(control_arm["decisions"]) + int(candidate_arm["decisions"])
        if broken_input:
            verdict = "BROKEN_INPUT"
        elif comparable == 0:
            verdict = "INSUFFICIENT_OUTCOMES" if paper_decisions > 0 else "WAITING_FOR_SAMPLE"
        elif not sample_adequate:
            verdict = "WAITING_FOR_SAMPLE"
        else:
            control_mean = paired_row["control_mean_net_r"]
            candidate_mean = paired_row["candidate_mean_net_r"]
            if control_mean is None or candidate_mean is None:
                verdict = "INSUFFICIENT_OUTCOMES"
            elif candidate_mean > control_mean:
                verdict = "CANDIDATE_WINNING"
            else:
                verdict = "CONTROL_WINNING"
        rows.append(
            {
                "version": version,
                "verdict": verdict,
                "shadow": shadow_row,
                "paper": {"control": control_arm, "candidate": candidate_arm, **paired_row},
            }
        )
    return {
        "control_version": control_version,
        "min_comparable_sample": min_sample,
        "candidates": rows,
    }


def _serialize_backtest(run: BacktestRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": str(run.id),
        "label": run.label,
        "strategy": run.strategy,
        "period_from": run.period_from.isoformat(),
        "period_to": run.period_to.isoformat(),
        "trades": int(run.trades),
        "net_return": _number(run.net_return),
        "profit_factor": _number(run.profit_factor),
        "expectancy_r": _number(run.expectancy_r),
        "max_drawdown": _number(run.max_drawdown),
        "sharpe": _number(run.sharpe),
        "sortino": _number(run.sortino),
        "calmar": _number(run.calmar),
        "brier_score": _number(run.brier_score),
        "pbo": _number(run.pbo),
        "top5_contribution": _number(run.top5_contribution),
        "gate_passed": bool(run.gate_passed),
        "gate_detail": run.gate_detail_json or {},
        "report": run.report_json or {},
        "config_hash": run.config_hash,
        "engine_version": run.engine_version,
        "universe": list(run.universe_json or []),
        "created_at": run.created_at.isoformat(),
    }


def _backtest_snapshot(
    session: Session,
    *,
    market_marker: str,
    cfg: EngineConfig,
) -> dict[str, Any]:
    recent = session.execute(
        select(BacktestRun)
        .where(~BacktestRun.label.like(_RISK_RUN_PREFIX))
        .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
        .limit(100)
    ).scalars().all()
    latest = next(
        (
            run
            for run in recent
            if market_marker in {str(item).upper() for item in (run.universe_json or [])}
        ),
        None,
    )
    return {
        "latest": _serialize_backtest(latest),
        "walk_forward": dict(cfg.get("backtest.walk_forward")),
        "paper_gate": dict(cfg.get("backtest.paper_gate")),
        "live_gate": dict(cfg.get("backtest.live_gate")),
    }


def _bybit_data_readiness(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(DatasetSnapshot)
        .where(DatasetSnapshot.dataset_name.like("bybit:%:multistream"))
        .order_by(DatasetSnapshot.created_at.desc(), DatasetSnapshot.id.desc())
        .limit(300)
    ).scalars().all()
    latest: dict[str, DatasetSnapshot] = {}
    for row in rows:
        latest.setdefault(row.dataset_name, row)
    symbols: list[dict[str, Any]] = []
    for name in sorted(latest):
        row = latest[name]
        watermark = row.source_watermark or {}
        symbol = str(watermark.get("symbol") or name.split(":")[1])
        symbols.append(
            {
                "symbol": symbol,
                "status": str(watermark.get("readiness") or "UNKNOWN"),
                "snapshot_id": row.snapshot_id,
                "content_sha256": row.content_sha256,
                "tradable_at": row.tradable_at.isoformat(),
                "row_count": int(row.row_count),
                "coverage": list(watermark.get("coverage") or []),
            }
        )
    if not symbols:
        status = "NO_DATASET"
    elif all(item["status"] == "DATA_READY" for item in symbols):
        status = "DATA_READY"
    else:
        status = "DATA_BLOCKED"
    return {"status": status, "symbols": symbols}


def _risk_optimizer_snapshot(session: Session, *, cfg: EngineConfig) -> dict[str, Any]:
    champion = session.execute(
        select(ModelRegistry)
        .where(ModelRegistry.name == _RISK_MODEL_NAME, ModelRegistry.role == "champion")
        .order_by(ModelRegistry.promoted_at.desc().nullslast(), ModelRegistry.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    latest_run = session.execute(
        select(BacktestRun)
        .where(BacktestRun.label.like(_RISK_RUN_PREFIX))
        .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    optimizer_cfg = cfg.get("risk.management.optimizer")
    candidate_ids = [
        str(item.get("id"))
        for item in optimizer_cfg.get("candidates", [])
        if isinstance(item, dict) and item.get("id")
    ]
    champion_json = None
    if champion is not None:
        calibration = champion.calibration_json or {}
        champion_json = {
            "version": champion.version,
            "candidate_id": calibration.get("candidate_id"),
            "algorithm": champion.algorithm,
            "sample_size": int(champion.sample_size),
            "trained_from": champion.trained_from.isoformat(),
            "trained_to": champion.trained_to.isoformat(),
            "promoted_at": champion.promoted_at.isoformat() if champion.promoted_at else None,
            "metrics": calibration.get("metrics") or {},
            "llm_review": calibration.get("llm_review") or {},
            "absolute_risk_caps_changed": calibration.get("absolute_risk_caps_changed", False),
        }
    cadence_days = int(optimizer_cfg["cadence_days"])
    next_due = None
    if latest_run is not None and latest_run.created_at is not None:
        next_due = (latest_run.created_at + timedelta(days=cadence_days)).isoformat()
    return {
        "champion": champion_json,
        "latest_run": _serialize_backtest(latest_run),
        "next_due_at": next_due,
        "scheduled": True,
        "config": {
            "cadence_days": cadence_days,
            "min_samples": int(optimizer_cfg["min_samples"]),
            "min_oos_expectancy_improvement_r": float(optimizer_cfg["min_oos_expectancy_improvement_r"]),
            "candidate_ids": candidate_ids,
            "absolute_risk_caps_mutable": False,
        },
    }


def build_control_dashboard(
    session: Session,
    *,
    venue: ExternalVenue,
    window_hours: int = 168,
    now: datetime | None = None,
    cfg: EngineConfig | None = None,
) -> dict[str, Any]:
    """Aggregate one owner-facing venue snapshot without lifecycle side effects."""

    if not isinstance(session, Session):
        raise ValueError("session must be a SQLAlchemy Session")
    if window_hours < 1:
        raise ValueError("window_hours must be positive")
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    aliases, venue_enum, asset_class, market_marker = _venue_spec(venue)
    config = cfg or get_config()
    start = moment - timedelta(hours=window_hours)

    control = _control_funnel(session, start=start, venue_enum=venue_enum, asset_class=asset_class)
    shadow = _shadow_rows(session, start=start, venue_aliases=aliases)
    paper, paired, control_version = _paper_stats(session, start=start, venue_aliases=aliases)
    min_sample = int(config.get("backtest.paper_gate.min_trades_per_setup"))
    competition = _competition(
        shadow=shadow,
        paper=paper,
        paired=paired,
        control_version=control_version,
        min_sample=min_sample,
    )
    runtime_roles = compose_runtime_roles(
        competition,
        registry_roles=registry_role_map(session),
    )
    scan_funnel = dashboard_funnel(session, start=start) if venue == "BYBIT" else None
    data_readiness = _bybit_data_readiness(session) if venue == "BYBIT" else {
        "status": "NOT_APPLICABLE",
        "symbols": [],
    }

    runtime_evidence = (
        int(control["ideas_created"])
        + sum(int(row["observations"]) for row in shadow)
        + sum(int(arm["decisions"]) for arms in paper.values() for arm in arms.values())
    )
    candidate_rows = competition["candidates"]
    if any(row["verdict"] == "BROKEN_INPUT" for row in candidate_rows):
        health = "BROKEN_INPUT"
    elif venue == "BYBIT" and data_readiness["status"] == "DATA_BLOCKED":
        health = "BROKEN_INPUT"
    elif runtime_evidence == 0:
        health = "NO_SAMPLE"
    elif any(
        row["verdict"] in {"WAITING_FOR_SAMPLE", "INSUFFICIENT_OUTCOMES"}
        or int(row["shadow"]["unavailable"]) > 0
        for row in candidate_rows
    ):
        health = "DEGRADED"
    else:
        health = "OK"

    return {
        "generated_at": moment.astimezone(UTC).isoformat(),
        "venue": venue,
        "window_hours": int(window_hours),
        "health": health,
        "runtime_roles": runtime_roles,
        "funnel": {
            "control": control,
            "scan": scan_funnel,
            "candidates": shadow,
        },
        "competition": competition,
        "data_readiness": data_readiness,
        "backtest": _backtest_snapshot(session, market_marker=market_marker, cfg=config),
        "risk_optimizer": _risk_optimizer_snapshot(session, cfg=config),
    }


__all__ = ["ExternalVenue", "build_control_dashboard"]
