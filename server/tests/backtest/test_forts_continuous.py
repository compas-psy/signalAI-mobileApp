from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.backtest.forts_continuous import FuturesSegment, build_continuous_futures
from app.market.candles import Candle


def _bar(day: int, close: str, *, source: str = "moex-history") -> Candle:
    at = datetime(2026, 6, day, tzinfo=UTC)
    price = Decimal(close)
    return Candle(
        open_time=at,
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume_units=Decimal("100"),
        volume_notional=None,
        open_interest=Decimal("1000"),
        is_closed=True,
        source=source,
    )


def test_continuous_futures_preserves_contract_provenance_and_half_open_roll_boundary() -> None:
    roll = datetime(2026, 6, 15, tzinfo=UTC)
    first = FuturesSegment(
        contract_id="MOEX:FUT:RIM6",
        valid_from=datetime(2026, 6, 1, tzinfo=UTC),
        valid_until=roll,
        bars=(_bar(13, "100"), _bar(14, "101"), _bar(15, "999")),
    )
    second = FuturesSegment(
        contract_id="MOEX:FUT:RIU6",
        valid_from=roll,
        valid_until=datetime(2026, 9, 17, tzinfo=UTC),
        bars=(_bar(14, "888"), _bar(15, "110"), _bar(16, "111")),
    )

    result = build_continuous_futures((second, first))

    assert [row.bar.open_time.day for row in result] == [13, 14, 15, 16]
    assert [row.contract_id for row in result] == [
        "MOEX:FUT:RIM6",
        "MOEX:FUT:RIM6",
        "MOEX:FUT:RIU6",
        "MOEX:FUT:RIU6",
    ]
    assert result[1].segment_valid_until == roll
    assert result[2].segment_valid_until == datetime(2026, 9, 17, tzinfo=UTC)
    assert result[2].bar.close == Decimal("110")


def test_continuous_futures_rejects_overlapping_segments_and_duplicate_contract_ids() -> None:
    first = FuturesSegment(
        contract_id="MOEX:FUT:RIM6",
        valid_from=datetime(2026, 6, 1, tzinfo=UTC),
        valid_until=datetime(2026, 6, 16, tzinfo=UTC),
        bars=(_bar(14, "101"),),
    )
    overlap = FuturesSegment(
        contract_id="MOEX:FUT:RIU6",
        valid_from=datetime(2026, 6, 15, tzinfo=UTC),
        valid_until=datetime(2026, 9, 17, tzinfo=UTC),
        bars=(_bar(15, "110"),),
    )
    duplicate_id = FuturesSegment(
        contract_id="MOEX:FUT:RIM6",
        valid_from=datetime(2026, 6, 16, tzinfo=UTC),
        valid_until=datetime(2026, 9, 17, tzinfo=UTC),
        bars=(_bar(16, "111"),),
    )

    with pytest.raises(ValueError, match="overlap"):
        build_continuous_futures((first, overlap))
    with pytest.raises(ValueError, match="contract_id"):
        build_continuous_futures((first, duplicate_id))


def test_continuous_futures_keeps_gaps_and_never_synthesises_bars() -> None:
    first = FuturesSegment(
        contract_id="MOEX:FUT:RIM6",
        valid_from=datetime(2026, 6, 1, tzinfo=UTC),
        valid_until=datetime(2026, 6, 15, tzinfo=UTC),
        bars=(_bar(13, "100"),),
    )
    second = FuturesSegment(
        contract_id="MOEX:FUT:RIU6",
        valid_from=datetime(2026, 6, 15, tzinfo=UTC),
        valid_until=datetime(2026, 9, 17, tzinfo=UTC),
        bars=(_bar(16, "111"),),
    )

    result = build_continuous_futures((first, second))
    assert [row.bar.open_time.day for row in result] == [13, 16]
