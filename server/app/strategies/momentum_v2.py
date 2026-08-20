"""R4 time-series momentum/trend challenger.

The candidate is intentionally pure: it consumes already-normalized canonical
candles and returns ``StrategyResultV2`` evidence or ``None``.  It does not
size a position, choose leverage, create stops/targets, submit an order or
replace the frozen legacy trend-pullback CONTROL.

The first version is deliberately small and auditable:
* context/setup/trigger momentum must agree;
* momentum is normalized by the canonical Wilder ATR;
* the latest trigger close must break the prior range;
* directional efficiency rejects high-turnover chop;
* only closed bars available as of ``evaluated_at`` can participate.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from ..features.indicators import atr
from ..market.candles import Candle
from ..models.enums import Direction, QualityFlag
from .result_v2 import (
    DataQualityState,
    EntryHypothesis,
    ExplanationComponent,
    FeatureProvenance,
    StrategyHorizon,
    StrategyResultV2,
)

ATR_PERIOD = 14
MOMENTUM_LOOKBACK = 20
BREAKOUT_LOOKBACK = 20
MIN_NORMALIZED_MOMENTUM = Decimal("1")
MIN_BREAKOUT_ATR = Decimal("0.05")
MIN_EFFICIENCY = Decimal("0.55")

_BLOCKING_QUALITY_FLAGS = frozenset(
    {
        QualityFlag.STALE.value,
        QualityFlag.GAP.value,
        QualityFlag.OUTLIER.value,
        QualityFlag.SOURCE_CONFLICT.value,
        QualityFlag.PARTIAL_BAR.value,
        QualityFlag.SESSION_MISMATCH.value,
        QualityFlag.LOW_LIQUIDITY.value,
        QualityFlag.CORPORATE_ACTION_UNADJUSTED.value,
    }
)
_IGNORED_QUALITY_FLAGS = frozenset({QualityFlag.OI_UNAVAILABLE.value})


def evaluate_momentum_v2(
    *,
    instrument_id: str,
    context_bars: Sequence[Candle],
    setup_bars: Sequence[Candle],
    trigger_bars: Sequence[Candle],
    evaluated_at: datetime,
) -> StrategyResultV2 | None:
    """Evaluate one point-in-time momentum candidate.

    ``instrument_id`` is kept at the strategy boundary because SAI-053 is
    intended for both crypto and FORTS, while the numerical logic is venue
    neutral.  Venue/risk/execution policy stays downstream.
    """
    if not instrument_id.strip():
        raise ValueError("instrument_id must not be blank")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")

    context = _visible(context_bars, evaluated_at)
    setup = _visible(setup_bars, evaluated_at)
    trigger = _visible(trigger_bars, evaluated_at)
    minimum = max(ATR_PERIOD, MOMENTUM_LOOKBACK + 1, BREAKOUT_LOOKBACK + 1)
    if min(len(context), len(setup), len(trigger)) < minimum:
        return None

    context_momentum = _normalized_momentum(context)
    setup_momentum = _normalized_momentum(setup)
    trigger_momentum = _normalized_momentum(trigger)
    if context_momentum is None or setup_momentum is None or trigger_momentum is None:
        return None

    direction = _agreed_direction(
        context_momentum,
        setup_momentum,
        trigger_momentum,
    )
    if direction is None:
        return None

    trigger_atr = _latest_atr(trigger)
    if trigger_atr is None or trigger_atr <= 0:
        return None

    breakout_level, breakout_atr = _breakout(trigger, direction, trigger_atr)
    if breakout_atr < MIN_BREAKOUT_ATR:
        return None

    efficiency = _efficiency(trigger)
    if efficiency < MIN_EFFICIENCY:
        return None

    quality = _data_quality((*context, *setup, *trigger))
    entry = trigger[-1].close
    score, explanations = _score(
        context_momentum=context_momentum,
        setup_momentum=setup_momentum,
        trigger_momentum=trigger_momentum,
        breakout_atr=breakout_atr,
        efficiency=efficiency,
    )
    observed_at = max(context[-1].open_time, setup[-1].open_time, trigger[-1].open_time)

    rationale_side = "above" if direction is Direction.LONG else "below"
    invalidation_side = "below" if direction is Direction.LONG else "above"
    provenance = (
        _feature("context_momentum_atr", context_momentum, "context_closed_bars", observed_at, evaluated_at),
        _feature("setup_momentum_atr", setup_momentum, "setup_closed_bars", observed_at, evaluated_at),
        _feature("trigger_momentum_atr", trigger_momentum, "trigger_closed_bars", observed_at, evaluated_at),
        _feature("trigger_breakout_atr", breakout_atr, "trigger_closed_bars", observed_at, evaluated_at),
        _feature("trigger_efficiency", efficiency, "trigger_closed_bars", observed_at, evaluated_at),
        _feature("entry_reference", entry, "trigger_closed_bars", observed_at, evaluated_at),
    )

    return StrategyResultV2(
        strategy_family="MOMENTUM",
        strategy_version="momentum_v2",
        direction=direction,
        raw_edge_score=score,
        entry_hypothesis=EntryHypothesis(
            kind="BREAKOUT_CONFIRMATION",
            reference=entry,
            rationale=(
                f"closed trigger bar confirmed {rationale_side} "
                f"the prior {BREAKOUT_LOOKBACK}-bar range at {breakout_level}"
            ),
        ),
        invalidation=(
            f"candidate invalid if a trigger close returns {invalidation_side} "
            "the pre-breakout range or multi-horizon momentum loses agreement"
        ),
        horizon=StrategyHorizon(value=24, unit="HOURS"),
        feature_provenance=provenance,
        regime_compatibility=("TREND", "NORMAL_VOL", "HIGH_VOL"),
        data_quality_state=quality,
        explanation_components=explanations,
        evaluated_at=evaluated_at,
    )


def _visible(bars: Sequence[Candle], evaluated_at: datetime) -> list[Candle]:
    return sorted(
        (
            bar
            for bar in bars
            if bar.is_closed and bar.open_time <= evaluated_at
        ),
        key=lambda bar: bar.open_time,
    )


def _latest_atr(bars: Sequence[Candle]) -> Decimal | None:
    values = atr(bars, ATR_PERIOD)
    return values[-1] if values else None


def _normalized_momentum(bars: Sequence[Candle]) -> Decimal | None:
    latest_atr = _latest_atr(bars)
    if latest_atr is None or latest_atr <= 0:
        return None
    delta = bars[-1].close - bars[-1 - MOMENTUM_LOOKBACK].close
    return delta / latest_atr


def _agreed_direction(*momenta: Decimal) -> Direction | None:
    if all(value >= MIN_NORMALIZED_MOMENTUM for value in momenta):
        return Direction.LONG
    if all(value <= -MIN_NORMALIZED_MOMENTUM for value in momenta):
        return Direction.SHORT
    return None


def _breakout(
    bars: Sequence[Candle],
    direction: Direction,
    latest_atr: Decimal,
) -> tuple[Decimal, Decimal]:
    previous = bars[-1 - BREAKOUT_LOOKBACK : -1]
    close = bars[-1].close
    if direction is Direction.LONG:
        level = max(bar.high for bar in previous)
        distance = close - level
    else:
        level = min(bar.low for bar in previous)
        distance = level - close
    return level, distance / latest_atr


def _efficiency(bars: Sequence[Candle]) -> Decimal:
    window = bars[-1 - MOMENTUM_LOOKBACK :]
    closes = [bar.close for bar in window]
    travelled = sum(
        (abs(current - previous) for previous, current in zip(closes, closes[1:], strict=False)),
        Decimal(0),
    )
    if travelled == 0:
        return Decimal(0)
    return abs(closes[-1] - closes[0]) / travelled


def _data_quality(bars: Sequence[Candle]) -> DataQualityState:
    flags = {flag for bar in bars for flag in bar.quality_flags}
    if flags & _BLOCKING_QUALITY_FLAGS:
        return DataQualityState.BLOCKED
    if flags - _IGNORED_QUALITY_FLAGS:
        return DataQualityState.DEGRADED
    return DataQualityState.GOOD


def _score(
    *,
    context_momentum: Decimal,
    setup_momentum: Decimal,
    trigger_momentum: Decimal,
    breakout_atr: Decimal,
    efficiency: Decimal,
) -> tuple[Decimal, tuple[ExplanationComponent, ...]]:
    def bounded_strength(value: Decimal, scale: Decimal) -> Decimal:
        return min(abs(value) / scale, Decimal(1))

    components = (
        ExplanationComponent(
            name="context_trend",
            contribution=bounded_strength(context_momentum, Decimal(4)) * Decimal("0.25"),
            detail="context momentum normalized by Wilder ATR",
        ),
        ExplanationComponent(
            name="setup_trend",
            contribution=bounded_strength(setup_momentum, Decimal(4)) * Decimal("0.25"),
            detail="setup momentum normalized by Wilder ATR",
        ),
        ExplanationComponent(
            name="trigger_trend",
            contribution=bounded_strength(trigger_momentum, Decimal(4)) * Decimal("0.20"),
            detail="trigger momentum agrees with higher horizons",
        ),
        ExplanationComponent(
            name="breakout_confirmation",
            contribution=min(breakout_atr, Decimal(1)) * Decimal("0.20"),
            detail="closed trigger bar cleared the prior range",
        ),
        ExplanationComponent(
            name="anti_chop_efficiency",
            contribution=min(efficiency, Decimal(1)) * Decimal("0.10"),
            detail="directional efficiency penalizes back-and-forth movement",
        ),
    )
    total = sum((component.contribution for component in components), Decimal(0))
    return total.quantize(Decimal("0.0001")), components


def _feature(
    name: str,
    value: Decimal,
    source: str,
    observed_at: datetime,
    evaluated_at: datetime,
) -> FeatureProvenance:
    return FeatureProvenance(
        name=name,
        value=str(value),
        source=source,
        observed_at=observed_at,
        tradable_at=evaluated_at,
    )
