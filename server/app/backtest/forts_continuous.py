"""Fail-closed FORTS continuous-contract research series.

The builder does not manufacture prices, backfill gaps or write canonical market
bars.  Every retained bar keeps the exact exchange contract that was tradable
inside an explicit half-open segment.  ``segment_valid_until`` is carried into
walk-forward samples so an outcome horizon can be purged when it crosses a roll.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..market.candles import Candle


@dataclass(frozen=True, slots=True)
class FuturesSegment:
    contract_id: str
    valid_from: datetime
    valid_until: datetime
    bars: tuple[Candle, ...]

    def __post_init__(self) -> None:
        if not self.contract_id.strip():
            raise ValueError("contract_id is required")
        for name, value in (
            ("valid_from", self.valid_from),
            ("valid_until", self.valid_until),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")


@dataclass(frozen=True, slots=True)
class ContinuousFuturesBar:
    contract_id: str
    segment_valid_until: datetime
    bar: Candle


def build_continuous_futures(
    segments: tuple[FuturesSegment, ...] | list[FuturesSegment],
) -> tuple[ContinuousFuturesBar, ...]:
    """Select real closed bars from explicit, non-overlapping roll segments."""

    ordered = tuple(sorted(segments, key=lambda item: item.valid_from))
    ids = [item.contract_id for item in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("contract_id values must be unique in a continuous chain")

    for previous, current in zip(ordered, ordered[1:]):
        if current.valid_from < previous.valid_until:
            raise ValueError(
                f"FORTS segment overlap: {previous.contract_id} and {current.contract_id}"
            )

    output: list[ContinuousFuturesBar] = []
    seen_times: set[datetime] = set()
    for segment in ordered:
        times = [bar.open_time for bar in segment.bars]
        if times != sorted(times):
            raise ValueError(f"bars for {segment.contract_id} must be chronological")
        if len(times) != len(set(times)):
            raise ValueError(f"bars for {segment.contract_id} contain duplicate timestamps")

        for bar in segment.bars:
            if bar.open_time.tzinfo is None or bar.open_time.utcoffset() is None:
                raise ValueError("FORTS historical bars must be timezone-aware")
            if not bar.is_closed:
                continue
            if not (segment.valid_from <= bar.open_time < segment.valid_until):
                continue
            if bar.open_time in seen_times:
                raise ValueError("continuous FORTS chain contains duplicate bar timestamp")
            seen_times.add(bar.open_time)
            output.append(
                ContinuousFuturesBar(
                    contract_id=segment.contract_id,
                    segment_valid_until=segment.valid_until,
                    bar=bar,
                )
            )

    output.sort(key=lambda item: item.bar.open_time)
    return tuple(output)


__all__ = ["ContinuousFuturesBar", "FuturesSegment", "build_continuous_futures"]
