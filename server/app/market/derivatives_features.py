"""Pure derivatives-flow features from point-in-time candle history.

The live scanner and historical replay can call the same function.  No network
or database access occurs here, which prevents a backtest from silently
substituting present-day Bybit facts for historical observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from statistics import fmean, pstdev
from typing import Sequence

from .candles import Candle


@dataclass(frozen=True, slots=True)
class DerivativesFeatureSnapshot:
    """Current price/OI impulse normalized by recent aligned changes."""

    price_change_z: float | None
    oi_change_z: float | None
    samples: int
    reason: str | None = None


def _relative_change(previous: Decimal, current: Decimal) -> float | None:
    if previous <= 0 or current <= 0:
        return None
    value = float((current - previous) / previous)
    return value if isfinite(value) else None


def _zscore_current(values: Sequence[float]) -> float:
    """Return the latest observation's population z-score.

    A truly flat history is neutral rather than unavailable.  The epsilon also
    absorbs harmless float conversion noise from Decimal percentage changes.
    """
    if not values:
        return 0.0
    sigma = pstdev(values)
    if sigma <= 1e-12:
        return 0.0
    return (values[-1] - fmean(values)) / sigma


def derivatives_change_z(
    bars: Sequence[Candle],
    *,
    lookback: int = 48,
    min_samples: int = 20,
) -> DerivativesFeatureSnapshot:
    """Calculate aligned H1 price and open-interest change z-scores.

    Only consecutive observations containing valid positive OI participate.
    Both signals fail closed together: classifying price flow while OI is
    absent would falsely present a derivatives confirmation as measured.
    """
    if lookback < 2:
        raise ValueError("lookback must be at least 2")
    if min_samples < 2:
        raise ValueError("min_samples must be at least 2")
    if min_samples > lookback:
        raise ValueError("min_samples must not exceed lookback")

    ordered = sorted(bars, key=lambda item: item.open_time)
    changes: list[tuple[float, float]] = []
    for previous, current in zip(ordered, ordered[1:]):
        if previous.open_interest is None or current.open_interest is None:
            continue
        price_change = _relative_change(previous.close, current.close)
        oi_change = _relative_change(previous.open_interest, current.open_interest)
        if price_change is None or oi_change is None:
            continue
        changes.append((price_change, oi_change))

    sample = changes[-lookback:]
    if len(sample) < min_samples:
        return DerivativesFeatureSnapshot(
            price_change_z=None,
            oi_change_z=None,
            samples=len(sample),
            reason="OI_HISTORY_INSUFFICIENT",
        )

    price_changes = [item[0] for item in sample]
    oi_changes = [item[1] for item in sample]
    return DerivativesFeatureSnapshot(
        price_change_z=_zscore_current(price_changes),
        oi_change_z=_zscore_current(oi_changes),
        samples=len(sample),
        reason=None,
    )


__all__ = ["DerivativesFeatureSnapshot", "derivatives_change_z"]
