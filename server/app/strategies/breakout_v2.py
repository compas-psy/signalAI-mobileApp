"""R4 volatility-adjusted breakout challenger.

This module intentionally does not reuse or alter the legacy ``breakout_retest``
trade-plan strategy.  It consumes closed canonical candles plus point-in-time
market friction facts and returns only ``StrategyResultV2`` evidence.  Position
sizing, stops, targets, leverage, order intent and execution remain downstream.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
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
RANGE_LOOKBACK = 20
CONFIRMATION_BARS = 2
MIN_CONFIRMATION_ATR = Decimal("0.30")
MIN_CLOSE_LOCATION = Decimal("0.70")
MAX_SPREAD_BPS = Decimal("12")
MIN_COST_COVER_MULTIPLE = Decimal("3")
REGIME_COMPATIBILITY = ("TREND", "NORMAL_VOL", "HIGH_VOL")

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


@dataclass(frozen=True, slots=True)
class BreakoutMarketFacts:
    """Venue-neutral point-in-time friction and regime facts for breakout alpha."""

    spread_bps: Decimal
    round_trip_cost_bps: Decimal
    regime: str
    observed_at: datetime
    tradable_at: datetime
    source: str

    def __post_init__(self) -> None:
        _require_non_negative_decimal("spread_bps", self.spread_bps)
        _require_non_negative_decimal("round_trip_cost_bps", self.round_trip_cost_bps)
        _require_text("regime", self.regime)
        _require_aware_datetime("observed_at", self.observed_at)
        _require_aware_datetime("tradable_at", self.tradable_at)
        if self.tradable_at < self.observed_at:
            raise ValueError("tradable_at must not precede observed_at")
        _require_text("source", self.source)


def evaluate_breakout_v2(
    *,
    instrument_id: str,
    bars: Sequence[Candle],
    market: BreakoutMarketFacts,
    evaluated_at: datetime,
) -> StrategyResultV2 | None:
    """Evaluate a persistent, cost-aware breakout using visible closed bars only."""

    _require_text("instrument_id", instrument_id)
    _require_aware_datetime("evaluated_at", evaluated_at)
    if not isinstance(market, BreakoutMarketFacts):
        raise ValueError("market must be BreakoutMarketFacts")
    if market.observed_at > evaluated_at or market.tradable_at > evaluated_at:
        return None
    if market.regime not in REGIME_COMPATIBILITY:
        return None
    if market.spread_bps > MAX_SPREAD_BPS:
        return None

    visible = _visible(bars, evaluated_at)
    minimum = max(ATR_PERIOD, RANGE_LOOKBACK) + CONFIRMATION_BARS
    if len(visible) < minimum:
        return None

    # Freeze both range and ATR before the confirmation window.  The breakout
    # itself cannot inflate its denominator or move its own comparison level.
    baseline = visible[:-CONFIRMATION_BARS]
    confirmations = visible[-CONFIRMATION_BARS:]
    range_window = baseline[-RANGE_LOOKBACK:]
    baseline_atr = _latest_atr(baseline)
    if baseline_atr is None or baseline_atr <= 0:
        return None

    prior_high = max(bar.high for bar in range_window)
    prior_low = min(bar.low for bar in range_window)
    threshold = baseline_atr * MIN_CONFIRMATION_ATR
    direction = _confirmed_direction(
        confirmations,
        prior_high=prior_high,
        prior_low=prior_low,
        threshold=threshold,
    )
    if direction is None:
        return None

    latest = confirmations[-1]
    close_location = _directional_close_location(latest, direction)
    if close_location < MIN_CLOSE_LOCATION:
        return None

    latest_price = latest.close
    if latest_price <= 0:
        return None
    atr_move_bps = baseline_atr / latest_price * Decimal("10000")
    required_move_bps = market.round_trip_cost_bps * MIN_COST_COVER_MULTIPLE
    if atr_move_bps < required_move_bps:
        return None

    breakout_level = prior_high if direction is Direction.LONG else prior_low
    first_confirmation_atr = _confirmation_distance_atr(
        confirmations[0], direction, breakout_level, baseline_atr
    )
    second_confirmation_atr = _confirmation_distance_atr(
        confirmations[1], direction, breakout_level, baseline_atr
    )
    quality = _data_quality(visible)
    score, explanations = _score(
        first_confirmation_atr=first_confirmation_atr,
        second_confirmation_atr=second_confirmation_atr,
        close_location=close_location,
        atr_move_bps=atr_move_bps,
        round_trip_cost_bps=market.round_trip_cost_bps,
        spread_bps=market.spread_bps,
    )

    side = "above" if direction is Direction.LONG else "below"
    inside = "below" if direction is Direction.LONG else "above"
    observed_at = latest.open_time
    provenance = (
        _bar_feature("breakout_level", breakout_level, observed_at, evaluated_at),
        _bar_feature(
            "first_confirmation_atr", first_confirmation_atr, observed_at, evaluated_at
        ),
        _bar_feature(
            "second_confirmation_atr", second_confirmation_atr, observed_at, evaluated_at
        ),
        _bar_feature("close_location", close_location, observed_at, evaluated_at),
        _bar_feature("atr_move_bps", atr_move_bps, observed_at, evaluated_at),
        _market_feature("spread_bps", market.spread_bps, market),
        _market_feature("round_trip_cost_bps", market.round_trip_cost_bps, market),
        FeatureProvenance(
            name="regime",
            value=market.regime,
            source=market.source,
            observed_at=market.observed_at,
            tradable_at=market.tradable_at,
        ),
    )

    return StrategyResultV2(
        strategy_family="BREAKOUT",
        strategy_version="breakout_v2",
        direction=direction,
        raw_edge_score=score,
        entry_hypothesis=EntryHypothesis(
            kind="BREAKOUT_CONTINUATION",
            reference=latest_price,
            rationale=(
                f"two closed bars confirmed {side} the prior "
                f"{RANGE_LOOKBACK}-bar range at {breakout_level}"
            ),
        ),
        invalidation=(
            f"candidate invalid if a closed bar returns {inside} the pre-breakout "
            "range or regime/liquidity/cost conditions no longer qualify"
        ),
        horizon=StrategyHorizon(value=12, unit="HOURS"),
        feature_provenance=provenance,
        regime_compatibility=REGIME_COMPATIBILITY,
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


def _confirmed_direction(
    confirmations: Sequence[Candle],
    *,
    prior_high: Decimal,
    prior_low: Decimal,
    threshold: Decimal,
) -> Direction | None:
    if all(bar.close >= prior_high + threshold for bar in confirmations):
        return Direction.LONG
    if all(bar.close <= prior_low - threshold for bar in confirmations):
        return Direction.SHORT
    return None


def _directional_close_location(bar: Candle, direction: Direction) -> Decimal:
    span = bar.high - bar.low
    if span <= 0:
        return Decimal(0)
    if direction is Direction.LONG:
        return (bar.close - bar.low) / span
    return (bar.high - bar.close) / span


def _confirmation_distance_atr(
    bar: Candle,
    direction: Direction,
    breakout_level: Decimal,
    baseline_atr: Decimal,
) -> Decimal:
    distance = (
        bar.close - breakout_level
        if direction is Direction.LONG
        else breakout_level - bar.close
    )
    return distance / baseline_atr


def _data_quality(bars: Sequence[Candle]) -> DataQualityState:
    flags = {flag for bar in bars for flag in bar.quality_flags}
    if flags & _BLOCKING_QUALITY_FLAGS:
        return DataQualityState.BLOCKED
    if flags - _IGNORED_QUALITY_FLAGS:
        return DataQualityState.DEGRADED
    return DataQualityState.GOOD


def _score(
    *,
    first_confirmation_atr: Decimal,
    second_confirmation_atr: Decimal,
    close_location: Decimal,
    atr_move_bps: Decimal,
    round_trip_cost_bps: Decimal,
    spread_bps: Decimal,
) -> tuple[Decimal, tuple[ExplanationComponent, ...]]:
    confirmation_strength = min(
        (first_confirmation_atr + second_confirmation_atr) / Decimal("2"),
        Decimal("1.5"),
    ) / Decimal("1.5")
    close_strength = min(max(close_location, Decimal(0)), Decimal(1))
    if round_trip_cost_bps == 0:
        cost_cover = Decimal(1)
    else:
        cost_cover = min(
            atr_move_bps / (round_trip_cost_bps * MIN_COST_COVER_MULTIPLE),
            Decimal(2),
        ) / Decimal(2)
    spread_quality = max(
        Decimal(0), Decimal(1) - spread_bps / MAX_SPREAD_BPS
    )

    components = (
        ExplanationComponent(
            name="persistent_breakout",
            contribution=confirmation_strength * Decimal("0.45"),
            detail="two closed confirmations clear the pre-breakout range in ATR units",
        ),
        ExplanationComponent(
            name="directional_close",
            contribution=close_strength * Decimal("0.20"),
            detail="confirmation closes near the directional edge of its bar",
        ),
        ExplanationComponent(
            name="cost_cover",
            contribution=cost_cover * Decimal("0.25"),
            detail="one baseline ATR retains multiple round-trip cost units",
        ),
        ExplanationComponent(
            name="spread_quality",
            contribution=spread_quality * Decimal("0.10"),
            detail="quoted spread remains below the breakout liquidity ceiling",
        ),
    )
    total = sum((component.contribution for component in components), Decimal(0))
    return total.quantize(Decimal("0.0001")), components


def _bar_feature(
    name: str,
    value: Decimal,
    observed_at: datetime,
    evaluated_at: datetime,
) -> FeatureProvenance:
    return FeatureProvenance(
        name=name,
        value=str(value),
        source="closed_canonical_bars",
        observed_at=observed_at,
        tradable_at=evaluated_at,
    )


def _market_feature(
    name: str,
    value: Decimal,
    market: BreakoutMarketFacts,
) -> FeatureProvenance:
    return FeatureProvenance(
        name=name,
        value=str(value),
        source=market.source,
        observed_at=market.observed_at,
        tradable_at=market.tradable_at,
    )


def _require_text(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")


def _require_non_negative_decimal(label: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{label} must be a finite non-negative Decimal")


def _require_aware_datetime(label: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


__all__ = ["BreakoutMarketFacts", "evaluate_breakout_v2"]
