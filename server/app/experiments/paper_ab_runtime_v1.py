"""Runtime bridge from Shadow observations to isolated Paper A/B evidence.

The runtime is deliberately counterfactual measurement only.  It never creates
owner TradeIdea/PaperTrade rows and never writes execution state.  Candidate
and frozen-control arms are recorded as immutable decisions; a second immutable
outcome fact appears only when an honest label is available.  No outcome row
means PENDING.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from ..features.indicators import atr
from ..market.economic_events import load_owned_calendar
from ..models import Bar, Instrument, PaperAbDecision, PaperAbOutcome, ShadowObservation
from ..models.enums import Direction, IdeaStatus, Timeframe
from ..pipeline.scan import scan_instrument
from ..risk.sizing import RiskState
from ..shadow.collector_v1 import _load_visible_bars, _regime_for
from ..strategy_identity import LEGACY_CONTROL_VERSION
from .paper_ab_v1 import (
    PaperAbArmObservation,
    PaperAbArmRole,
    PaperAbEvidenceStatus,
    PaperAbRollingReport,
    build_rolling_paper_report,
)

ControlProvider = Callable[[Session, Instrument, datetime], "PaperAbControlDecision"]
RiskUnitProvider = Callable[[Session, str, datetime], Decimal | None]
RegimeProvider = Callable[[Session, Instrument, datetime], str]

_PAIR_SCHEMA = "paper_ab_pair_v1"
_METRIC_SCHEMA = "paper_directional_alpha_r_v1"
_CANDIDATE_HORIZON_MINUTES = {
    "momentum_v2": 24 * 60,
    "mean_reversion_v1": 12 * 60,
    "breakout_v2": 12 * 60,
    # Carry is intentionally not price-labelled by this runtime; the value is
    # only the paired control observation window until a settled-funding path
    # is available as a first-class input.
    "crypto_carry_v1": 24 * 60,
}


@dataclass(frozen=True, slots=True)
class PaperAbControlDecision:
    signal_emitted: bool
    direction: Direction | None = None
    entry_reference: Decimal | None = None
    confidence: Decimal | None = None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.signal_emitted, bool):
            raise ValueError("signal_emitted must be bool")
        if self.unavailable_reason is not None:
            if not self.unavailable_reason.strip():
                raise ValueError("unavailable_reason must not be blank")
            if self.signal_emitted or self.direction is not None or self.entry_reference is not None:
                raise ValueError("unavailable control decision cannot contain a signal")
            return
        if self.signal_emitted:
            if not isinstance(self.direction, Direction):
                raise ValueError("emitted control signal requires direction")
            _positive_decimal("entry_reference", self.entry_reference)
        elif self.direction is not None or self.entry_reference is not None:
            raise ValueError("no-signal control decision cannot contain trade values")
        if self.confidence is not None:
            if not isinstance(self.confidence, Decimal) or not self.confidence.is_finite():
                raise ValueError("confidence must be a finite Decimal")
            if not Decimal(0) <= self.confidence <= Decimal(1):
                raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class PaperAbSeedReport:
    considered_shadow: int
    seeded_pairs: int
    immediate_outcomes: int

    def summary(self) -> str:
        return (
            f"shadow {self.considered_shadow}, новых A/B пар {self.seeded_pairs}, "
            f"сразу размечено {self.immediate_outcomes}"
        )


@dataclass(frozen=True, slots=True)
class PaperAbResolveReport:
    resolved: int
    pending: int
    unavailable: int

    def summary(self) -> str:
        return (
            f"paper A/B исходов {self.resolved}, ожидают {self.pending}, "
            f"входы недоступны {self.unavailable}"
        )


@dataclass(frozen=True, slots=True)
class PaperAbCycleReport:
    seed: PaperAbSeedReport
    resolve: PaperAbResolveReport

    def summary(self) -> str:
        return f"{self.seed.summary()}; {self.resolve.summary()}"


def seed_paper_ab(
    session: Session,
    *,
    evaluated_at: datetime | None = None,
    control_provider: ControlProvider | None = None,
    risk_unit_provider: RiskUnitProvider | None = None,
    regime_provider: RegimeProvider | None = None,
    cfg: EngineConfig | None = None,
) -> PaperAbSeedReport:
    """Append one frozen-control/candidate Paper pair per unseen Shadow row."""

    _session(session)
    moment = evaluated_at or datetime.now(UTC)
    _aware("evaluated_at", moment)
    config = cfg or get_config()
    control = control_provider or _default_control_provider(config)
    risk_unit = risk_unit_provider or _default_risk_unit
    regime = regime_provider or _default_regime_provider(config)

    shadows = list(
        session.execute(
            select(ShadowObservation)
            .where(ShadowObservation.evaluated_at <= moment)
            .order_by(ShadowObservation.evaluated_at, ShadowObservation.observation_key)
        ).scalars()
    )
    seeded = 0
    immediate = 0
    for shadow in shadows:
        pair_key = _pair_key(shadow)
        existing = list(
            session.execute(
                select(PaperAbDecision).where(PaperAbDecision.pair_key == pair_key)
            ).scalars()
        )
        if existing:
            if len(existing) != 2 or {row.arm_role for row in existing} != {
                PaperAbArmRole.CONTROL.value,
                PaperAbArmRole.CANDIDATE.value,
            }:
                raise ValueError(f"partial Paper A/B pair exists: {pair_key}")
            continue

        instrument = session.execute(
            select(Instrument).where(Instrument.instrument_id == shadow.instrument_id)
        ).scalar_one_or_none()
        if instrument is None:
            raise ValueError(f"Paper A/B instrument missing: {shadow.instrument_id}")
        horizon = _horizon_minutes(shadow.strategy_version)
        regime_label = regime(session, instrument, shadow.evaluated_at)
        if not isinstance(regime_label, str) or not regime_label.strip():
            raise ValueError("regime_provider must return non-blank text")
        risk_value = risk_unit(session, shadow.instrument_id, shadow.evaluated_at)
        if risk_value is not None:
            _positive_decimal("risk_unit_price", risk_value)
        round_trip_cost = _round_trip_cost_bps(instrument, shadow.cost_model_hash)
        control_fact = control(session, instrument, shadow.evaluated_at)
        if not isinstance(control_fact, PaperAbControlDecision):
            raise ValueError("control_provider must return PaperAbControlDecision")

        candidate = PaperAbDecision(
            decision_key=_decision_key(pair_key, PaperAbArmRole.CANDIDATE),
            pair_key=pair_key,
            candidate_version=shadow.strategy_version,
            arm_role=PaperAbArmRole.CANDIDATE.value,
            strategy_version=shadow.strategy_version,
            instrument_id=shadow.instrument_id,
            venue=shadow.venue,
            regime=regime_label,
            decision_at=shadow.evaluated_at,
            market_snapshot_hash=shadow.market_snapshot_hash,
            cost_model_hash=shadow.cost_model_hash,
            signal_emitted=shadow.signal_emitted,
            direction=shadow.direction if shadow.signal_emitted else None,
            entry_reference=shadow.entry_reference if shadow.signal_emitted else None,
            confidence=None,
            horizon_minutes=horizon,
            risk_unit_price=risk_value,
            round_trip_cost_bps=round_trip_cost,
        )
        control_row = PaperAbDecision(
            decision_key=_decision_key(pair_key, PaperAbArmRole.CONTROL),
            pair_key=pair_key,
            candidate_version=shadow.strategy_version,
            arm_role=PaperAbArmRole.CONTROL.value,
            strategy_version=LEGACY_CONTROL_VERSION,
            instrument_id=shadow.instrument_id,
            venue=shadow.venue,
            regime=regime_label,
            decision_at=shadow.evaluated_at,
            market_snapshot_hash=shadow.market_snapshot_hash,
            cost_model_hash=shadow.cost_model_hash,
            signal_emitted=control_fact.signal_emitted,
            direction=(
                control_fact.direction.value if control_fact.signal_emitted else None
            ),
            entry_reference=(
                control_fact.entry_reference if control_fact.signal_emitted else None
            ),
            confidence=control_fact.confidence,
            horizon_minutes=horizon,
            risk_unit_price=risk_value,
            round_trip_cost_bps=round_trip_cost,
        )
        session.add_all((control_row, candidate))
        session.flush()

        immediate += _seed_immediate_candidate_outcome(session, candidate, shadow)
        immediate += _seed_immediate_control_outcome(session, control_row, control_fact)
        seeded += 1

    return PaperAbSeedReport(
        considered_shadow=len(shadows),
        seeded_pairs=seeded,
        immediate_outcomes=immediate,
    )


def resolve_paper_ab(
    session: Session,
    *,
    as_of: datetime | None = None,
) -> PaperAbResolveReport:
    """Append mature directional-alpha labels; leave not-yet-mature rows pending."""

    _session(session)
    moment = as_of or datetime.now(UTC)
    _aware("as_of", moment)
    decisions = list(
        session.execute(
            select(PaperAbDecision)
            .outerjoin(PaperAbOutcome, PaperAbOutcome.decision_id == PaperAbDecision.id)
            .where(PaperAbOutcome.id.is_(None))
            .order_by(PaperAbDecision.decision_at, PaperAbDecision.decision_key)
        ).scalars()
    )
    resolved = 0
    pending = 0
    unavailable = 0
    for decision in decisions:
        # Defensive consistency: seeding normally writes these terminal facts
        # immediately, but a partially committed/manual row still fails closed.
        if not decision.signal_emitted:
            _add_evaluated_outcome(
                session,
                decision,
                net_r=None,
                exit_reference=None,
                outcome_at=decision.decision_at,
            )
            resolved += 1
            continue
        if decision.strategy_version == "crypto_carry_v1" and decision.arm_role == PaperAbArmRole.CANDIDATE.value:
            _add_unavailable_outcome(
                session,
                decision,
                "CARRY_REALIZED_FUNDING_PATH_UNAVAILABLE",
                moment,
            )
            unavailable += 1
            continue
        if decision.round_trip_cost_bps is None:
            _add_unavailable_outcome(
                session, decision, "ROUND_TRIP_COST_UNAVAILABLE", moment
            )
            unavailable += 1
            continue
        if decision.risk_unit_price is None or decision.risk_unit_price <= 0:
            _add_unavailable_outcome(session, decision, "RISK_UNIT_UNAVAILABLE", moment)
            unavailable += 1
            continue

        maturity = decision.decision_at + timedelta(minutes=decision.horizon_minutes)
        if moment < maturity:
            pending += 1
            continue
        exit_bar = session.execute(
            select(Bar)
            .where(
                Bar.instrument_id == decision.instrument_id,
                Bar.timeframe == Timeframe.H1,
                Bar.is_closed.is_(True),
                Bar.open_time >= maturity,
                Bar.open_time <= moment,
            )
            .order_by(Bar.open_time)
            .limit(1)
        ).scalar_one_or_none()
        if exit_bar is None:
            pending += 1
            continue

        entry = decision.entry_reference
        if entry is None or entry <= 0:
            _add_unavailable_outcome(session, decision, "ENTRY_REFERENCE_UNAVAILABLE", moment)
            unavailable += 1
            continue
        risk = decision.risk_unit_price
        if decision.direction == Direction.LONG.value:
            gross_r = (exit_bar.close - entry) / risk
        elif decision.direction == Direction.SHORT.value:
            gross_r = (entry - exit_bar.close) / risk
        else:
            _add_unavailable_outcome(session, decision, "DIRECTION_UNAVAILABLE", moment)
            unavailable += 1
            continue
        cost_r = (
            entry * decision.round_trip_cost_bps / Decimal("10000") / risk
        )
        net_r = gross_r - cost_r
        _add_evaluated_outcome(
            session,
            decision,
            net_r=net_r,
            exit_reference=exit_bar.close,
            outcome_at=exit_bar.open_time,
        )
        resolved += 1

    return PaperAbResolveReport(
        resolved=resolved,
        pending=pending,
        unavailable=unavailable,
    )


def build_persisted_rolling_report(
    session: Session,
    *,
    candidate_version: str,
    as_of: datetime,
    window: timedelta,
    min_sample: int = 30,
) -> PaperAbRollingReport:
    """Materialize immutable DB facts into the pure rolling paired evaluator."""

    _session(session)
    _aware("as_of", as_of)
    start = as_of.astimezone(UTC) - window
    decisions = list(
        session.execute(
            select(PaperAbDecision)
            .where(
                PaperAbDecision.candidate_version == candidate_version,
                PaperAbDecision.decision_at >= start,
                PaperAbDecision.decision_at <= as_of,
            )
            .order_by(PaperAbDecision.decision_at, PaperAbDecision.decision_key)
        ).scalars()
    )
    if not decisions:
        return build_rolling_paper_report(
            (),
            (),
            control_version=LEGACY_CONTROL_VERSION,
            candidate_version=candidate_version,
            as_of=as_of,
            window=window,
            min_sample=min_sample,
        )
    outcome_by_decision = {
        row.decision_id: row
        for row in session.execute(
            select(PaperAbOutcome).where(
                PaperAbOutcome.decision_id.in_([decision.id for decision in decisions])
            )
        ).scalars()
    }
    control_rows: list[PaperAbArmObservation] = []
    candidate_rows: list[PaperAbArmObservation] = []
    for decision in decisions:
        observation = _materialize_arm(
            decision,
            outcome_by_decision.get(decision.id),
        )
        if decision.arm_role == PaperAbArmRole.CONTROL.value:
            control_rows.append(observation)
        elif decision.arm_role == PaperAbArmRole.CANDIDATE.value:
            candidate_rows.append(observation)
        else:
            raise ValueError(f"unknown Paper A/B arm role: {decision.arm_role}")

    return build_rolling_paper_report(
        control_rows,
        candidate_rows,
        control_version=LEGACY_CONTROL_VERSION,
        candidate_version=candidate_version,
        as_of=as_of,
        window=window,
        min_sample=min_sample,
    )


def run_paper_ab_cycle(session: Session, *, as_of: datetime | None = None) -> PaperAbCycleReport:
    """Production scheduler entry point: seed unseen Shadow rows, then resolve."""

    moment = as_of or datetime.now(UTC)
    seed = seed_paper_ab(session, evaluated_at=moment)
    resolve = resolve_paper_ab(session, as_of=moment)
    return PaperAbCycleReport(seed=seed, resolve=resolve)


def _seed_immediate_candidate_outcome(
    session: Session,
    decision: PaperAbDecision,
    shadow: ShadowObservation,
) -> int:
    if shadow.evidence_status == PaperAbEvidenceStatus.INPUT_UNAVAILABLE.value:
        _add_unavailable_outcome(
            session,
            decision,
            shadow.reason_code or "SHADOW_INPUT_UNAVAILABLE",
            shadow.evaluated_at,
        )
        return 1
    if shadow.evidence_status != PaperAbEvidenceStatus.EVALUATED.value:
        raise ValueError(f"unsupported Shadow evidence status: {shadow.evidence_status}")
    if not shadow.signal_emitted:
        _add_evaluated_outcome(
            session,
            decision,
            net_r=None,
            exit_reference=None,
            outcome_at=shadow.evaluated_at,
        )
        return 1
    if shadow.strategy_version == "crypto_carry_v1":
        _add_unavailable_outcome(
            session,
            decision,
            "CARRY_REALIZED_FUNDING_PATH_UNAVAILABLE",
            shadow.evaluated_at,
        )
        return 1
    return _ensure_trade_inputs_or_unavailable(session, decision)


def _seed_immediate_control_outcome(
    session: Session,
    decision: PaperAbDecision,
    fact: PaperAbControlDecision,
) -> int:
    if fact.unavailable_reason is not None:
        _add_unavailable_outcome(
            session, decision, fact.unavailable_reason, decision.decision_at
        )
        return 1
    if not fact.signal_emitted:
        _add_evaluated_outcome(
            session,
            decision,
            net_r=None,
            exit_reference=None,
            outcome_at=decision.decision_at,
        )
        return 1
    return _ensure_trade_inputs_or_unavailable(session, decision)


def _ensure_trade_inputs_or_unavailable(session: Session, decision: PaperAbDecision) -> int:
    if decision.round_trip_cost_bps is None:
        _add_unavailable_outcome(
            session,
            decision,
            "ROUND_TRIP_COST_UNAVAILABLE",
            decision.decision_at,
        )
        return 1
    if decision.risk_unit_price is None or decision.risk_unit_price <= 0:
        _add_unavailable_outcome(
            session, decision, "RISK_UNIT_UNAVAILABLE", decision.decision_at
        )
        return 1
    return 0


def _materialize_arm(
    decision: PaperAbDecision,
    outcome: PaperAbOutcome | None,
) -> PaperAbArmObservation:
    if outcome is None:
        status = PaperAbEvidenceStatus.PENDING
        net_r = None
        reason = None
    else:
        status = PaperAbEvidenceStatus(outcome.evidence_status)
        net_r = outcome.net_r
        reason = outcome.reason_code
    return PaperAbArmObservation(
        pair_key=decision.pair_key,
        candidate_version=decision.candidate_version,
        arm_role=PaperAbArmRole(decision.arm_role),
        strategy_version=decision.strategy_version,
        instrument_id=decision.instrument_id,
        venue=decision.venue,
        regime=decision.regime,
        decision_at=decision.decision_at,
        market_snapshot_hash=decision.market_snapshot_hash,
        cost_model_hash=decision.cost_model_hash,
        signal_emitted=decision.signal_emitted,
        net_r=net_r,
        confidence=decision.confidence,
        evidence_status=status,
        reason_code=reason,
    )


def _add_evaluated_outcome(
    session: Session,
    decision: PaperAbDecision,
    *,
    net_r: Decimal | None,
    exit_reference: Decimal | None,
    outcome_at: datetime,
) -> None:
    session.add(
        PaperAbOutcome(
            decision_id=decision.id,
            evidence_status=PaperAbEvidenceStatus.EVALUATED.value,
            net_r=net_r,
            exit_reference=exit_reference,
            outcome_at=outcome_at,
            reason_code=None,
        )
    )


def _add_unavailable_outcome(
    session: Session,
    decision: PaperAbDecision,
    reason: str,
    outcome_at: datetime,
) -> None:
    if not reason.strip():
        raise ValueError("unavailable outcome requires reason")
    session.add(
        PaperAbOutcome(
            decision_id=decision.id,
            evidence_status=PaperAbEvidenceStatus.INPUT_UNAVAILABLE.value,
            net_r=None,
            exit_reference=None,
            outcome_at=outcome_at,
            reason_code=reason,
        )
    )


def _default_control_provider(cfg: EngineConfig) -> ControlProvider:
    def provider(
        session: Session,
        instrument: Instrument,
        evaluated_at: datetime,
    ) -> PaperAbControlDecision:
        # ``scan_instrument`` historically reads the newest closed bars in the
        # DB.  Replaying it after later bars arrived would leak future data.
        # The scheduler evaluates new Shadow rows immediately, so current rows
        # can use the deployed control; stale/backfill rows fail closed until a
        # dedicated historical control replay exists.
        newest_h1 = session.execute(
            select(func.max(Bar.open_time)).where(
                Bar.instrument_id == instrument.instrument_id,
                Bar.timeframe == Timeframe.H1,
                Bar.is_closed.is_(True),
            )
        ).scalar_one_or_none()
        if newest_h1 is not None and newest_h1 > evaluated_at:
            return PaperAbControlDecision(
                signal_emitted=False,
                unavailable_reason="CONTROL_REPLAY_NOT_POINT_IN_TIME",
            )

        event_calendar = load_owned_calendar(now=evaluated_at)
        idea, skipped, _rejections = scan_instrument(
            session,
            instrument,
            cfg=cfg,
            risk_state=RiskState(risk_equity=Decimal("100000")),
            now=evaluated_at,
            event_calendar=event_calendar,
        )
        if idea is None:
            if any(item.stage == "данные" for item in skipped):
                return PaperAbControlDecision(
                    signal_emitted=False,
                    unavailable_reason="CONTROL_BAR_HISTORY_INSUFFICIENT",
                )
            return PaperAbControlDecision(signal_emitted=False)
        emitted = idea.status is IdeaStatus.TRIGGERED
        if not emitted:
            return PaperAbControlDecision(signal_emitted=False)
        return PaperAbControlDecision(
            signal_emitted=True,
            direction=idea.direction,
            entry_reference=idea.entry_reference,
            confidence=idea.confidence,
        )

    return provider


def _default_risk_unit(
    session: Session,
    instrument_id: str,
    evaluated_at: datetime,
) -> Decimal | None:
    bars = _load_visible_bars(
        session,
        instrument_id,
        Timeframe.H1,
        evaluated_at=evaluated_at,
        limit=100,
    )
    values = atr(bars, 14)
    return next((value for value in reversed(values) if value is not None and value > 0), None)


def _default_regime_provider(cfg: EngineConfig) -> RegimeProvider:
    def provider(session: Session, instrument: Instrument, evaluated_at: datetime) -> str:
        context = _load_visible_bars(
            session,
            instrument.instrument_id,
            Timeframe.D1,
            evaluated_at=evaluated_at,
            limit=400,
        )
        if len(context) < 60:
            return "UNCLASSIFIED"
        value = _regime_for(instrument, context=context, cfg=cfg)
        return f"{value.trend.value}|{value.volatility.value}|{value.liquidity.value}"

    return provider


def _round_trip_cost_bps(
    instrument: Instrument,
    expected_hash: str,
) -> Decimal | None:
    metadata = instrument.metadata_json or {}
    cost = metadata.get("shadow_cost_model") or {}
    explicit_hash = cost.get("cost_model_hash")
    if isinstance(explicit_hash, str) and explicit_hash.lower() != expected_hash.lower():
        return None
    value = _decimal(cost.get("round_trip_cost_bps"))
    if value is None or value < 0:
        return None
    return value


def _pair_key(shadow: ShadowObservation) -> str:
    payload = {
        "schema": _PAIR_SCHEMA,
        "shadow_observation_key": shadow.observation_key,
        "candidate_version": shadow.strategy_version,
        "metric": _METRIC_SCHEMA,
    }
    return _hash(payload)


def _decision_key(pair_key: str, role: PaperAbArmRole) -> str:
    return _hash({"pair_key": pair_key, "arm_role": role.value})


def _hash(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def _horizon_minutes(version: str) -> int:
    try:
        return _CANDIDATE_HORIZON_MINUTES[version]
    except KeyError as exc:
        raise ValueError(f"unsupported Paper A/B candidate version: {version}") from exc


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _positive_decimal(name: str, value: Decimal | None) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be a finite positive Decimal")


def _session(value: Session) -> None:
    if not isinstance(value, Session):
        raise ValueError("session must be a SQLAlchemy Session")


def _aware(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "PaperAbControlDecision",
    "PaperAbCycleReport",
    "PaperAbResolveReport",
    "PaperAbSeedReport",
    "build_persisted_rolling_report",
    "resolve_paper_ab",
    "run_paper_ab_cycle",
    "seed_paper_ab",
]
