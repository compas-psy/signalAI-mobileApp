"""Technical timing context for Russian-equity research hypotheses.

This module deliberately does less than a trading strategy.  It answers a
single, auditable question: is the latest *closed daily bar* technically
supportive of the economic direction of an existing hypothesis?  It never
produces a side, order, price or position size, and it is not evidence for the
3-2-1 promotion gate.

The score is deterministic and built from five visible measurements:
EMA(20), EMA(50), ATR(14), the distance from EMA(20) in ATRs, a 20-session
breakout and relative volume.  A high value therefore means "useful timing
for further research without obvious extension", not "the market has priced
the thesis correctly".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..features.indicators import atr, ema, relative_volume
from ..market.candles import Candle
from ..models import Bar
from ..models.enums import ResearchDirection, Timeframe

MarketContextState = Literal[
    "aligned_entry",
    "aligned_watch",
    "extended",
    "conflicting",
    "insufficient_history",
]

MIN_BARS = 80
MAX_BARS = 260
BREAKOUT_WINDOW = 20
VOLUME_CONFIRMATION = Decimal("1.25")
EXTENDED_ATR = Decimal("2.00")
MATERIAL_SCORE_STEP = Decimal("0.05")


@dataclass(frozen=True, slots=True)
class MarketContext:
    """A timing-only market snapshot attached to a research hypothesis."""

    score: Decimal | None
    state: MarketContextState
    detail: dict


def _decimal(value: Decimal | float | int | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _number(value: Decimal | None, places: str = "0.0001") -> str | None:
    """JSON-safe, reproducible rendering without binary-float artefacts."""
    if value is None:
        return None
    return format(value.quantize(Decimal(places), rounding=ROUND_HALF_UP), "f")


def _clamp(value: Decimal) -> Decimal:
    return max(Decimal(0), min(Decimal(1), value))


def _insufficient(
    *,
    instrument_id: str,
    bars: int,
    as_of: str | None,
    reason: str,
) -> MarketContext:
    return MarketContext(
        score=None,
        state="insufficient_history",
        detail={
            "kind": "market_context",
            "role": "research_timing_overlay",
            "not_a_trade_signal": True,
            "instrument_id": instrument_id,
            "timeframe": Timeframe.D1.value,
            "as_of": as_of,
            "bar_count": bars,
            "required_bars": MIN_BARS,
            "reason": reason,
        },
    )


def _valid_equity(instrument_id: str) -> bool:
    parts = instrument_id.split(":")
    return (
        len(parts) == 3
        and parts[0] == "MOEX"
        and parts[1] == "EQ"
        and bool(parts[2])
    )


def evaluate(
    bars: Sequence[Candle | Bar],
    *,
    instrument_id: str,
    direction: ResearchDirection,
) -> MarketContext:
    """Evaluate closed D1 bars in chronological order.

    ``bars`` may be ORM ``Bar`` rows or canonical ``Candle`` instances so
    the calculation itself stays unit-testable without a database.  Forming
    bars are removed even when a caller has already filtered them.
    """
    source = sorted(
        (
            bar
            for bar in bars
            if bool(bar.is_closed)
            and str(getattr(bar, "timeframe", Timeframe.D1))
            == Timeframe.D1.value
            and getattr(bar, "instrument_id", instrument_id) == instrument_id
        ),
        key=lambda bar: bar.open_time,
    )
    as_of = source[-1].open_time.isoformat() if source else None

    if not _valid_equity(instrument_id):
        return _insufficient(
            instrument_id=instrument_id,
            bars=len(source),
            as_of=as_of,
            reason="unsupported_instrument",
        )
    if direction not in (
        ResearchDirection.POSITIVE,
        ResearchDirection.NEGATIVE,
    ):
        return _insufficient(
            instrument_id=instrument_id,
            bars=len(source),
            as_of=as_of,
            reason="directional_overlay_not_applicable",
        )
    if len(source) < MIN_BARS:
        return _insufficient(
            instrument_id=instrument_id,
            bars=len(source),
            as_of=as_of,
            reason="not_enough_closed_daily_bars",
        )

    candles = [
        Candle(
            open_time=bar.open_time,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume_units=bar.volume_units,
            volume_notional=getattr(bar, "volume_notional", None),
            open_interest=getattr(bar, "open_interest", None),
            is_closed=True,
            source=getattr(bar, "source", ""),
            quality_flags=tuple(getattr(bar, "quality_flags", ()) or ()),
        )
        for bar in source
    ]
    closes = [bar.close for bar in candles]
    ema20 = ema(closes, 20)[-1]
    ema50 = ema(closes, 50)[-1]
    atr14 = atr(candles, 14)[-1]
    rel_volume_raw = relative_volume(candles, window=20)[-1]
    rel_volume = _decimal(rel_volume_raw)

    if ema20 is None or ema50 is None or atr14 is None or atr14 <= 0:
        return _insufficient(
            instrument_id=instrument_id,
            bars=len(source),
            as_of=as_of,
            reason="indicators_unavailable",
        )

    sign = (
        Decimal(1)
        if direction is ResearchDirection.POSITIVE
        else Decimal(-1)
    )
    close = candles[-1].close
    distance_atr = sign * (close - ema20) / atr14
    trend_gap_atr = sign * (ema20 - ema50) / atr14

    previous = candles[-(BREAKOUT_WINDOW + 1) : -1]
    breakout_level = (
        max(bar.high for bar in previous)
        if direction is ResearchDirection.POSITIVE
        else min(bar.low for bar in previous)
    )
    breakout = (
        close >= breakout_level
        if direction is ResearchDirection.POSITIVE
        else close <= breakout_level
    )
    volume_confirmed = bool(
        breakout and rel_volume is not None and rel_volume >= VOLUME_CONFIRMATION
    )

    trend_aligned = trend_gap_atr > 0
    pullback_zone = Decimal("-0.35") <= distance_atr <= Decimal("0.85")
    if trend_gap_atr < 0:
        state: MarketContextState = "conflicting"
    elif distance_atr > EXTENDED_ATR:
        state = "extended"
    elif trend_aligned and (pullback_zone or volume_confirmed):
        state = "aligned_entry"
    else:
        state = "aligned_watch"

    # Component scores are intentionally simple and included in ``detail``.
    # State caps guarantee that an opposed trend or an extended price can
    # never masquerade as a high-quality timing context.
    if trend_gap_atr >= Decimal("0.50"):
        trend_component = Decimal(1)
    elif trend_gap_atr > 0:
        trend_component = Decimal("0.70") + Decimal("0.60") * trend_gap_atr
    elif trend_gap_atr == 0:
        trend_component = Decimal("0.50")
    else:
        trend_component = _clamp(Decimal("0.50") + trend_gap_atr)

    if Decimal("-0.35") <= distance_atr <= Decimal("0.85"):
        location_component = Decimal(1)
    elif distance_atr < Decimal("-0.35"):
        location_component = _clamp(
            Decimal(1) - (Decimal("-0.35") - distance_atr) / Decimal("1.50")
        )
    else:
        location_component = _clamp(
            Decimal(1) - (distance_atr - Decimal("0.85")) / Decimal("1.50")
        )

    breakout_component = (
        Decimal(1)
        if volume_confirmed
        else Decimal("0.65")
        if breakout
        else Decimal("0.45")
    )
    if rel_volume is None:
        volume_component = Decimal("0.50")
    elif rel_volume >= VOLUME_CONFIRMATION:
        volume_component = Decimal(1)
    elif rel_volume >= Decimal("0.80"):
        volume_component = Decimal("0.65")
    else:
        volume_component = Decimal("0.35")

    raw_score = (
        Decimal("0.40") * trend_component
        + Decimal("0.35") * location_component
        + Decimal("0.15") * breakout_component
        + Decimal("0.10") * volume_component
    )
    if state == "conflicting":
        raw_score = min(raw_score, Decimal("0.25"))
    elif state == "extended":
        raw_score = min(raw_score, Decimal("0.45"))
    score = _clamp(raw_score).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    detail = {
        "kind": "market_context",
        "role": "research_timing_overlay",
        "not_a_trade_signal": True,
        "instrument_id": instrument_id,
        "timeframe": Timeframe.D1.value,
        "as_of": as_of,
        "bar_count": len(source),
        "direction": direction.value,
        "state": state,
        "close": _number(close),
        "ema20": _number(ema20),
        "ema50": _number(ema50),
        "atr14": _number(atr14),
        "distance_from_ema20_atr": _number(distance_atr),
        "ema20_ema50_gap_atr": _number(trend_gap_atr),
        "breakout_window": BREAKOUT_WINDOW,
        "breakout_level": _number(breakout_level),
        "directional_breakout": breakout,
        "relative_volume_20d": _number(rel_volume),
        "volume_confirmation_threshold": _number(VOLUME_CONFIRMATION, "0.00"),
        "volume_confirmed": volume_confirmed,
        "components": {
            "trend": _number(trend_component, "0.01"),
            "location": _number(location_component, "0.01"),
            "breakout": _number(breakout_component, "0.01"),
            "relative_volume": _number(volume_component, "0.01"),
        },
    }
    return MarketContext(score=score, state=state, detail=detail)


def for_hypothesis(
    session: Session,
    *,
    instrument_id: str,
    direction: ResearchDirection,
) -> MarketContext:
    """Load the latest closed D1 history for one exact MOEX equity."""
    if not _valid_equity(instrument_id):
        return _insufficient(
            instrument_id=instrument_id,
            bars=0,
            as_of=None,
            reason="unsupported_instrument",
        )
    rows = list(
        session.execute(
            select(Bar)
            .where(
                Bar.instrument_id == instrument_id,
                Bar.timeframe == Timeframe.D1,
                Bar.is_closed.is_(True),
            )
            .order_by(Bar.open_time.desc())
            .limit(MAX_BARS)
        ).scalars()
    )
    return evaluate(
        list(reversed(rows)),
        instrument_id=instrument_id,
        direction=direction,
    )


__all__ = [
    "BREAKOUT_WINDOW",
    "EXTENDED_ATR",
    "MATERIAL_SCORE_STEP",
    "MIN_BARS",
    "MarketContext",
    "MarketContextState",
    "evaluate",
    "for_hypothesis",
]
