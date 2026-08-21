"""Point-in-time R4 Shadow collector.

The collector evaluates challenger strategies on the same canonical closed-bar
store used by the live scanner, but persists only :class:`ShadowObservation`
measurement facts.  It never creates ``TradeIdea``/paper/notification/execution
entities.

Missing market inputs are explicit ``INPUT_UNAVAILABLE`` observations rather
than fake no-signal outcomes.  This distinction is essential for later OOS
sample denominators, especially for funding/carry where a missing funding feed
must not count as a strategy decision.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import EngineConfig, get_config
from ..experiments.candidate_oos_batch_v1 import R4_CANDIDATE_VERSIONS
from ..market.candles import Candle, resample_hours
from ..market.derivatives import CryptoCarryMarketFacts
from ..models import Bar, Instrument
from ..models.enums import (
    AssetClass,
    DerivativesFlow,
    LiquidityRegime,
    Timeframe,
    TrendRegime,
    VolatilityRegime,
)
from ..pipeline.scan import _liquidity_inputs
from ..regime.classifier import (
    RegimeResult,
    classify_liquidity,
    classify_trend,
    classify_volatility,
)
from ..strategies.breakout_v2 import BreakoutMarketFacts, evaluate_breakout_v2
from ..strategies.crypto_carry_v1 import evaluate_crypto_carry_v1
from ..strategies.mean_reversion_v1 import evaluate_mean_reversion_v1
from ..strategies.momentum_v2 import evaluate_momentum_v2
from .runtime_v1 import (
    ShadowCandidateObservation,
    ShadowEvaluationInput,
    ShadowEvidenceStatus,
    evaluate_shadow_candidates,
)
from .store_v1 import persist_shadow_observations

FactsProvider = Callable[[Instrument, datetime], "ShadowSupplementalFacts"]

_CONTEXT_LIMIT = 400
_TRIGGER_LIMIT = 800
_SETUP_HOURS = 4
# Live scan itself needs 60 D1 bars before it can make a regime decision.  Use
# the same floor rather than allowing a challenger to receive a richer/looser
# context than the control path.
_MIN_CONTEXT_BARS = 60
_MIN_MOMENTUM_BARS = 21
_MIN_MEAN_REVERSION_BARS = 20
_MIN_BREAKOUT_BARS = 22


@dataclass(frozen=True, slots=True)
class ShadowSupplementalFacts:
    """External point-in-time facts not contained in canonical OHLCV bars.

    Cost values are optional on purpose.  The collector does not invent them.
    A strategy that requires an absent value is recorded as INPUT_UNAVAILABLE.
    ``cost_model_hash`` still identifies the supplied measurement context.
    """

    cost_model_hash: str
    spread_bps: Decimal | None = None
    round_trip_cost_bps: Decimal | None = None
    crypto_carry_facts: CryptoCarryMarketFacts | None = None
    carry_execution_cost_bps: Decimal | None = None
    carry_hedge_cost_bps_per_interval: Decimal | None = None
    carry_uncertainty_bps_per_interval: Decimal | None = None
    carry_unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        _require_sha256("cost_model_hash", self.cost_model_hash)
        for name in (
            "spread_bps",
            "round_trip_cost_bps",
            "carry_execution_cost_bps",
            "carry_hedge_cost_bps_per_interval",
            "carry_uncertainty_bps_per_interval",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_non_negative_decimal(name, value)
        if self.crypto_carry_facts is not None and not isinstance(
            self.crypto_carry_facts, CryptoCarryMarketFacts
        ):
            raise ValueError("crypto_carry_facts must be CryptoCarryMarketFacts")
        if self.carry_unavailable_reason is not None and not self.carry_unavailable_reason.strip():
            raise ValueError("carry_unavailable_reason must not be blank")


@dataclass(frozen=True, slots=True)
class ShadowCollectionReport:
    instruments: int
    observations: int
    evaluated: int
    unavailable: int
    signals: int

    def summary(self) -> str:
        return (
            f"инструментов {self.instruments}, shadow-наблюдений {self.observations}, "
            f"оценено {self.evaluated}, входы недоступны {self.unavailable}, "
            f"кандидат-сигналов {self.signals}"
        )


def collect_shadow(
    session: Session,
    *,
    evaluated_at: datetime | None = None,
    facts_provider: FactsProvider | None = None,
    cfg: EngineConfig | None = None,
) -> ShadowCollectionReport:
    """Evaluate and append R4 Shadow observations for the tradable universe."""

    if not isinstance(session, Session):
        raise ValueError("session must be a SQLAlchemy Session")
    moment = evaluated_at or datetime.now(UTC)
    _require_aware_datetime("evaluated_at", moment)
    provider = facts_provider or _metadata_facts
    config = cfg or get_config()

    instruments = list(
        session.execute(
            select(Instrument)
            .where(Instrument.in_universe.is_(True), Instrument.is_tradable.is_(True))
            .order_by(Instrument.instrument_id)
        ).scalars()
    )

    all_observations: list[ShadowCandidateObservation] = []
    for instrument in instruments:
        context = _load_visible_bars(
            session,
            instrument.instrument_id,
            Timeframe.D1,
            evaluated_at=moment,
            limit=_CONTEXT_LIMIT,
        )
        trigger = _load_visible_bars(
            session,
            instrument.instrument_id,
            Timeframe.H1,
            evaluated_at=moment,
            limit=_TRIGGER_LIMIT,
        )
        setup = resample_hours(trigger, _SETUP_HOURS, session_start_hour_utc=6)
        market_snapshot_hash = _market_snapshot_hash(
            instrument.instrument_id,
            context=context,
            trigger=trigger,
        )
        facts = provider(instrument, moment)
        if not isinstance(facts, ShadowSupplementalFacts):
            raise ValueError("facts_provider must return ShadowSupplementalFacts")

        candidates = []
        unavailable: dict[str, str] = {}

        if (
            len(context) < _MIN_MOMENTUM_BARS
            or len(setup) < _MIN_MOMENTUM_BARS
            or len(trigger) < _MIN_MOMENTUM_BARS
        ):
            unavailable["momentum_v2"] = "BAR_HISTORY_INSUFFICIENT"
        else:
            candidate = evaluate_momentum_v2(
                instrument_id=instrument.instrument_id,
                context_bars=context,
                setup_bars=setup,
                trigger_bars=trigger,
                evaluated_at=moment,
            )
            if candidate is not None:
                candidates.append(candidate)

        regime: RegimeResult | None = None
        if len(context) >= _MIN_CONTEXT_BARS:
            regime = _regime_for(instrument, context=context, cfg=config)

        if len(setup) < _MIN_MEAN_REVERSION_BARS or regime is None:
            unavailable["mean_reversion_v1"] = "BAR_HISTORY_INSUFFICIENT"
        else:
            candidate = evaluate_mean_reversion_v1(
                instrument_id=instrument.instrument_id,
                bars=setup,
                regime=regime,
                evaluated_at=moment,
            )
            if candidate is not None:
                candidates.append(candidate)

        if len(setup) < _MIN_BREAKOUT_BARS or regime is None:
            unavailable["breakout_v2"] = "BAR_HISTORY_INSUFFICIENT"
        elif facts.spread_bps is None or facts.round_trip_cost_bps is None:
            unavailable["breakout_v2"] = "COST_FACTS_UNAVAILABLE"
        else:
            candidate = evaluate_breakout_v2(
                instrument_id=instrument.instrument_id,
                bars=setup,
                market=BreakoutMarketFacts(
                    spread_bps=facts.spread_bps,
                    round_trip_cost_bps=facts.round_trip_cost_bps,
                    regime=_breakout_regime(regime),
                    observed_at=moment,
                    tradable_at=moment,
                    source="shadow_supplemental_facts_v1",
                ),
                evaluated_at=moment,
            )
            if candidate is not None:
                candidates.append(candidate)

        if instrument.asset_class is not AssetClass.CRYPTO_PERPETUAL:
            unavailable["crypto_carry_v1"] = "INSTRUMENT_SCOPE_UNSUPPORTED"
        elif facts.crypto_carry_facts is None:
            unavailable["crypto_carry_v1"] = (
                facts.carry_unavailable_reason or "FUNDING_FACTS_UNAVAILABLE"
            )
        elif any(
            value is None
            for value in (
                facts.carry_execution_cost_bps,
                facts.carry_hedge_cost_bps_per_interval,
                facts.carry_uncertainty_bps_per_interval,
            )
        ):
            unavailable["crypto_carry_v1"] = "CARRY_COST_FACTS_UNAVAILABLE"
        else:
            candidate = evaluate_crypto_carry_v1(
                facts=facts.crypto_carry_facts,
                execution_cost_bps=facts.carry_execution_cost_bps,
                hedge_carry_bps_per_interval=facts.carry_hedge_cost_bps_per_interval,
                funding_uncertainty_bps_per_interval=facts.carry_uncertainty_bps_per_interval,
                evaluated_at=moment,
            )
            if candidate is not None:
                candidates.append(candidate)

        observations = evaluate_shadow_candidates(
            ShadowEvaluationInput(
                instrument_id=instrument.instrument_id,
                venue=instrument.venue.value,
                market_snapshot_hash=market_snapshot_hash,
                cost_model_hash=facts.cost_model_hash,
                evaluated_at=moment,
                candidate_versions=R4_CANDIDATE_VERSIONS,
                candidates=tuple(candidates),
                unavailable_reasons=tuple(
                    (version, unavailable[version])
                    for version in R4_CANDIDATE_VERSIONS
                    if version in unavailable
                ),
            )
        )
        persist_shadow_observations(session, observations)
        all_observations.extend(observations)

    return ShadowCollectionReport(
        instruments=len(instruments),
        observations=len(all_observations),
        evaluated=sum(
            item.evidence_status is ShadowEvidenceStatus.EVALUATED
            for item in all_observations
        ),
        unavailable=sum(
            item.evidence_status is ShadowEvidenceStatus.INPUT_UNAVAILABLE
            for item in all_observations
        ),
        signals=sum(item.signal_emitted for item in all_observations),
    )


def _load_visible_bars(
    session: Session,
    instrument_id: str,
    timeframe: Timeframe,
    *,
    evaluated_at: datetime,
    limit: int,
) -> list[Candle]:
    rows = list(
        session.execute(
            select(Bar)
            .where(
                Bar.instrument_id == instrument_id,
                Bar.timeframe == timeframe,
                Bar.is_closed.is_(True),
                Bar.open_time <= evaluated_at,
            )
            .order_by(Bar.open_time.desc())
            .limit(limit)
        ).scalars()
    )
    rows.reverse()
    return [
        Candle(
            open_time=row.open_time,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume_units=row.volume_units,
            volume_notional=row.volume_notional,
            open_interest=row.open_interest,
            is_closed=True,
            source=row.source,
            quality_flags=tuple(row.quality_flags or ()),
        )
        for row in rows
    ]


def _regime_for(
    instrument: Instrument,
    *,
    context: list[Candle],
    cfg: EngineConfig,
) -> RegimeResult:
    spread, turnover = _liquidity_inputs(instrument, context)
    liquidity, _ = classify_liquidity(
        relative_spread=spread,
        median_notional=turnover,
        min_notional=Decimal(
            str(cfg.get("universe.futures.min_median_daily_notional_rub"))
        ),
        max_spread=float(cfg.get("universe.futures.max_median_relative_spread")),
    )
    trend, score, signals, detail = classify_trend(
        context,
        ema_fast=int(cfg.get("regime.ema_fast")),
        ema_slow=int(cfg.get("regime.ema_slow")),
        adx_period=int(cfg.get("regime.adx_period")),
        adx_trend_min=float(cfg.get("regime.adx_trend_min")),
    )
    volatility, _ = classify_volatility(context)
    return RegimeResult(
        trend=trend,
        trend_score=score,
        volatility=volatility,
        liquidity=liquidity,
        derivatives_flow=DerivativesFlow.NEUTRAL,
        signals=signals,
        detail=detail,
    )


def _breakout_regime(regime: RegimeResult) -> str:
    if regime.trend in (TrendRegime.UPTREND, TrendRegime.DOWNTREND):
        return "TREND"
    if regime.volatility is VolatilityRegime.HIGH:
        return "HIGH_VOL"
    if regime.volatility is VolatilityRegime.NORMAL:
        return "NORMAL_VOL"
    return regime.trend.value


def _market_snapshot_hash(
    instrument_id: str,
    *,
    context: list[Candle],
    trigger: list[Candle],
) -> str:
    def encode(timeframe: str, bar: Candle) -> dict[str, object]:
        return {
            "timeframe": timeframe,
            "open_time": bar.open_time.isoformat(),
            "open": str(bar.open),
            "high": str(bar.high),
            "low": str(bar.low),
            "close": str(bar.close),
            "volume_units": None if bar.volume_units is None else str(bar.volume_units),
            "volume_notional": (
                None if bar.volume_notional is None else str(bar.volume_notional)
            ),
            "open_interest": None if bar.open_interest is None else str(bar.open_interest),
            "source": bar.source,
            "quality_flags": sorted(bar.quality_flags),
        }

    payload = {
        "schema": "shadow_market_snapshot_v1",
        "instrument_id": instrument_id,
        "bars": [
            *(encode(Timeframe.D1.value, bar) for bar in context),
            *(encode(Timeframe.H1.value, bar) for bar in trigger),
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _metadata_facts(
    instrument: Instrument,
    evaluated_at: datetime,
) -> ShadowSupplementalFacts:
    """Resolve only facts already present in instrument metadata.

    No brokerage/cost number is guessed.  If the metadata lacks an all-in
    round-trip cost, breakout/carry remain explicitly unavailable while
    bar-only strategies can still accumulate honest Shadow observations.
    """

    metadata = instrument.metadata_json or {}
    admission = metadata.get("admission") or {}
    cost_meta = metadata.get("shadow_cost_model") or {}

    relative_spread = _decimal(
        admission.get("relative_spread_snapshot") or metadata.get("spread_snapshot")
    )
    spread_bps = (
        relative_spread * Decimal("10000") if relative_spread is not None else None
    )
    round_trip = _decimal(cost_meta.get("round_trip_cost_bps"))

    explicit_hash = cost_meta.get("cost_model_hash")
    if isinstance(explicit_hash, str) and _is_sha256(explicit_hash):
        cost_hash = explicit_hash.lower()
    else:
        identity = json.dumps(
            {
                "schema": "shadow_metadata_cost_context_v1",
                "venue": instrument.venue.value,
                "spread_bps": None if spread_bps is None else str(spread_bps),
                "round_trip_cost_bps": None if round_trip is None else str(round_trip),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        cost_hash = sha256(identity.encode("utf-8")).hexdigest()

    return ShadowSupplementalFacts(
        cost_model_hash=cost_hash,
        spread_bps=spread_bps,
        round_trip_cost_bps=round_trip,
        crypto_carry_facts=None,
        carry_unavailable_reason="FUNDING_FACTS_UNAVAILABLE",
    )


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


def _is_sha256(value: str) -> bool:
    if len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _require_sha256(name: str, value: str) -> None:
    if not isinstance(value, str) or not _is_sha256(value):
        raise ValueError(f"{name} must be a 64-character SHA-256 identity")


def _require_non_negative_decimal(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite non-negative Decimal")


def _require_aware_datetime(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


__all__ = [
    "FactsProvider",
    "ShadowCollectionReport",
    "ShadowSupplementalFacts",
    "collect_shadow",
]
