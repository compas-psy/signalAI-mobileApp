"""Independent closed-bar watermarks for the two trading lanes.

The scanner may still evaluate the whole tradable universe in one pass, but
its *wake-up condition* must not be one global max timestamp.  FORTS and
crypto can publish the same H1 open_time at different wall-clock moments.  A
single max timestamp therefore let the first venue suppress the second.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Bar, Instrument
from ..models.enums import AssetClass, Timeframe

Watermark = tuple[datetime, int]
Watermarks = dict[str, Watermark]


def changed_lanes(before: Watermarks, after: Watermarks) -> tuple[str, ...]:
    """Return lanes whose closed-bar identity changed.

    Count is deliberately part of the identity.  If FORTS H1 10:00 lands
    first and crypto H1 10:00 arrives later, the lane timestamp is equal but
    the crypto row count changes, so the second arrival still wakes scan.
    """
    keys = set(before) | set(after)
    return tuple(sorted(key for key in keys if before.get(key) != after.get(key)))


def snapshot(session: Session) -> Watermarks:
    """Latest closed H1 plus row count, independently for FORTS and crypto."""
    rows = session.execute(
        select(
            Instrument.asset_class,
            func.max(Bar.open_time),
            func.count(Bar.open_time),
        )
        .join(Instrument, Instrument.instrument_id == Bar.instrument_id)
        .where(
            Bar.timeframe == Timeframe.H1,
            Bar.is_closed.is_(True),
            Instrument.asset_class.in_(
                (AssetClass.FUTURES, AssetClass.CRYPTO_PERPETUAL)
            ),
        )
        .group_by(Instrument.asset_class)
    ).all()

    result: Watermarks = {}
    for asset_class, latest, count in rows:
        if latest is None:
            continue
        lane = (
            "forts"
            if asset_class == AssetClass.FUTURES
            else "crypto"
        )
        result[lane] = (latest, int(count))
    return result


__all__ = ["Watermark", "Watermarks", "changed_lanes", "snapshot"]
