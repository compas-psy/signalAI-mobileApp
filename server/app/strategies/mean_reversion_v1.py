"""R4 range / mean-reversion challenger.

This module deliberately consumes an already-classified market regime.  It does
not try to infer RANGE itself (SAI-058 owns the future regime classifier), and
it never emits risk or execution instructions.  A candidate exists only when a
liquid low/normal-volatility range shows both a material excursion from a
robust center and closed-bar evidence of reversion.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from ..features.indicators import atr
from ..market.candles import Candle
from ..models.enums import (
    Direction,
    LiquidityRegime,
    TrendRegime,
    VolatilityRegime,
)
from ..regime.classifier import RegimeResult
from .result_v2 import (
    DataQualityState,
    EntryHypothesis,
    ExplanationComponent,
    FeatureProvenance,
    StrategyHorizon,
    StrategyResultV2,
)

ATR_PERIOD = 14
CENTER_LOOKBACK = 30
EXHAUSTION_WINDOW = 3
MIN_HISTORY = CENTER_LOOKBACK + EXHAUSTION_WINDOW
MIN_DEVIATION_ATR = Decimal("2")
MIN_REVERSION_PROGRESS = Decimal("0.35")
HARD_INVALIDATION_ATR = Decimal("0.50")

_ALLOWED_VOLATILITY = frozenset({VolatilityRegime.LOW, VolatilityRegime.NORMAL})
_ALLOWED_LIQUIDITY = frozenset({LiquidityRegime.GOOD, LiquidityRegime.NORMAL})


def evaluate_mean_reversion_v1(
    *,
    instrument_id: str,
    bars: Sequence[Candle],
    regime: RegimeResult,
    evaluated_at: datetime,
) -> StrategyResultV2 | None:
    """Return range-reversion evidence or ``None``.

    The robust center and baseline ATR are calculated before the three-bar
    exhaustion/reversion window.  That prevents the excursion itself from
    moving the reference mean or inflating the volatility denominator enough
    to manufacture a weaker apparent deviation.
    """
    if not instrument_id.strip():
        raise ValueError("instrument_id must not be blank")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    if regime.trend is not TrendRegime.RANGE:
        return None
    if regime.volatility not in _ALLOWED_VOLATILITY:
        return None
    if regime.liquidity not in _ALLOWED_LIQUIDITY:
        return None

    visible = sorted(
        (bar for bar in bars if bar.is_closed and bar.open_time <= evaluated_at),
        key=lambda bar: bar.open_time,
    )
    if len(visible) < MIN_HISTORY:
        return None

    baseline = visible[:-EXHAUSTION_WINDOW]
    center_sample = baseline[-CENTER_LOOKBACK:]
    center = _median(tuple(bar.close for bar in center_sample))
    baseline_atr = _latest_atr(baseline)
    if center <= 0 or baseline_atr is None or baseline_atr <= 0:
        return None

    window = visible[-EXHAUSTION_WINDOW:]
    long_extreme = min(bar.low for bar in window)
    short_extreme = max(bar.high for bar in window)
    long_deviation = (center - long_extreme) / baseline_atr
    short_deviation = (short_extreme - center) / baseline_atr

    direction: Direction | None = None
    extreme: Decimal
    deviation_atr: Decimal
    if long_deviation >= MIN_DEVIATION_ATR and _confirmed_reversion(window, center, Direction.LONG):
        direction = Direction.LONG
        extreme = long_extreme
        deviation_atr = long_deviation
    elif short_deviation >= MIN_DEVIATION_ATR and _confirmed_reversion(
        window, center, Direction.SHORT
    ):
        direction = Direction.SHORT
        extreme = short_extreme
        deviation_atr = short_deviation
    else:
        return None

    entry = window[-1].close
    total_distance = abs(center - extreme)
    if total_distance == 0:
        return None
    reversion_progress = abs(entry - extreme) / total_distance
    if reversion_progress < MIN_REVERSION_PROGRESS:
        return None

    realized_vol_atr = baseline_atr / center
    if direction is Direction.LONG:
        hard_invalidation = extreme - baseline_atr * HARD_INVALIDATION_ATR
        rationale = "closed bars reverted upward after downside range exhaustion"
        invalidation_detail = f"close below {hard_invalidation} after downside exhaustion"
    else:
        hard_invalidation = extreme + baseline_atr * HARD_INVALIDATION_ATR
        rationale = "closed bars reverted downward after upside range exhaustion"
        invalidation_detail = f"close above {hard_invalidation} after upside exhaustion"

    score = _score(deviation_atr, reversion_progress, regime.liquidity)
    observed_at = window[-1].open_time
    provenance = (
        _feature("robust_center", center, "closed_bars_pre_exhaustion", observed_at, evaluated_at),
        _feature("deviation_atr", deviation_atr, "closed_bars_exhaustion", observed_at, evaluated_at),
        _feature("realized_vol_atr", realized_vol_atr, "wilder_atr_pre_exhaustion", observed_at, evaluated_at),
        _feature("reversion_progress", reversion_progress, "closed_bars_reversion", observed_at, evaluated_at),
        _feature("hard_invalidation", hard_invalidation, "closed_bars_exhaustion", observed_at, evaluated_at),
        _feature("entry_reference", entry, "closed_bars_reversion", observed_at, evaluated_at),
    )
    explanations = (
        ExplanationComponent(
            name="robust_deviation",
            contribution=min(deviation_atr / Decimal("4"), Decimal(1)) * Decimal("0.45"),
            detail="excursion from pre-exhaustion median normalized by Wilder ATR",
        ),
        ExplanationComponent(
            name="reversion_confirmation",
            contribution=min(reversion_progress, Decimal(1)) * Decimal("0.40"),
            detail="closed bars moved materially back toward the robust center",
        ),
        ExplanationComponent(
            name="liquidity_admission",
            contribution=Decimal("0.15") if regime.liquidity is LiquidityRegime.GOOD else Decimal("0.10"),
            detail=f"range candidate admitted with {regime.liquidity.value} liquidity",
        ),
    )

    return StrategyResultV2(
        strategy_family="MEAN_REVERSION",
        strategy_version="mean_reversion_v1",
        direction=direction,
        raw_edge_score=score,
        entry_hypothesis=EntryHypothesis(
            kind="REVERSION_CONFIRMATION",
            reference=entry,
            rationale=rationale,
        ),
        invalidation=f"Hard invalidation: {invalidation_detail}",
        horizon=StrategyHorizon(value=12, unit="HOURS"),
        feature_provenance=provenance,
        regime_compatibility=("RANGE", "LOW_VOL", "NORMAL_VOL"),
        data_quality_state=DataQualityState.GOOD,
        explanation_components=explanations,
        evaluated_at=evaluated_at,
    )


def _latest_atr(bars: Sequence[Candle]) -> Decimal | None:
    values = atr(bars, ATR_PERIOD)
    return next((value for value in reversed(values) if value is not None), None)


def _confirmed_reversion(
    window: Sequence[Candle], center: Decimal, direction: Direction
) -> bool:
    if len(window) < EXHAUSTION_WINDOW:
        return False
    closes = [bar.close for bar in window]
    if direction is Direction.LONG:
        return closes[0] < center and closes[0] < closes[1] < closes[2] <= center
    return closes[0] > center and closes[0] > closes[1] > closes[2] >= center


def _score(
    deviation_atr: Decimal,
    reversion_progress: Decimal,
    liquidity: LiquidityRegime,
) -> Decimal:
    deviation = min(deviation_atr / Decimal("4"), Decimal(1)) * Decimal("0.45")
    reversion = min(reversion_progress, Decimal(1)) * Decimal("0.40")
    liquidity_score = Decimal("0.15") if liquidity is LiquidityRegime.GOOD else Decimal("0.10")
    return (deviation + reversion + liquidity_score).quantize(Decimal("0.0001"))


def _median(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("median sample must not be empty")
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


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
