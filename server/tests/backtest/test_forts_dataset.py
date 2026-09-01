from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.forts_continuous import FuturesSegment
from app.backtest.forts_dataset import DATA_BLOCKED, DATA_READY, build_forts_manifest
from app.market.candles import Candle


END = datetime(2026, 8, 31, 18, 0, tzinfo=UTC)
START_36M = datetime(2023, 8, 31, 18, 0, tzinfo=UTC)


def _bar(at: datetime, price: str, *, oi: str | None = None) -> Candle:
    value = Decimal(price)
    return Candle(
        open_time=at,
        open=value,
        high=value + Decimal("1"),
        low=value - Decimal("1"),
        close=value,
        volume_units=Decimal("10"),
        volume_notional=Decimal("1000"),
        open_interest=None if oi is None else Decimal(oi),
        is_closed=True,
        source="moex-history",
        quality_flags=(),
    )


def _segments(start: datetime) -> tuple[FuturesSegment, ...]:
    roll = END - timedelta(days=90)
    return (
        FuturesSegment(
            contract_id="SiM6",
            valid_from=start,
            valid_until=roll,
            bars=(
                _bar(start, "90000"),
                _bar(roll - timedelta(hours=1), "91000"),
            ),
        ),
        FuturesSegment(
            contract_id="SiU6",
            valid_from=roll,
            valid_until=END,
            bars=(
                _bar(roll, "92000"),
                _bar(END - timedelta(hours=1), "93000"),
            ),
        ),
    )


def test_forts_dataset_fails_closed_when_continuous_history_is_shorter_than_36m() -> None:
    built = build_forts_manifest(
        root="SI",
        start_at=END - timedelta(days=365),
        end_at=END,
        h1_segments=_segments(END - timedelta(days=365)),
        d1_segments=_segments(END - timedelta(days=365)),
        daily_open_interest={
            END.date() - timedelta(days=1): Decimal("100000"),
        },
        min_history_months=36,
    )

    assert built.status == DATA_BLOCKED
    assert "HISTORY_LT_36M:continuous_h1" in built.blockers
    assert "HISTORY_LT_36M:continuous_d1" in built.blockers
    assert "HISTORY_LT_36M:daily_open_interest" in built.blockers


def test_forts_dataset_does_not_trust_segment_metadata_without_36m_bars() -> None:
    roll = END - timedelta(days=90)
    actual_start = END - timedelta(days=365)
    deceptive = (
        FuturesSegment(
            contract_id="SiM6",
            valid_from=START_36M,
            valid_until=roll,
            bars=(
                _bar(actual_start, "90000"),
                _bar(roll - timedelta(hours=1), "91000"),
            ),
        ),
        FuturesSegment(
            contract_id="SiU6",
            valid_from=roll,
            valid_until=END,
            bars=(
                _bar(roll, "92000"),
                _bar(END - timedelta(hours=1), "93000"),
            ),
        ),
    )

    built = build_forts_manifest(
        root="SI",
        start_at=START_36M,
        end_at=END,
        h1_segments=deceptive,
        d1_segments=deceptive,
        daily_open_interest={
            START_36M.date(): Decimal("80000"),
            (END - timedelta(days=1)).date(): Decimal("100000"),
        },
        min_history_months=36,
    )

    assert built.status == DATA_BLOCKED
    assert "HISTORY_LT_36M:continuous_h1" in built.blockers
    assert "HISTORY_LT_36M:continuous_d1" in built.blockers


def test_forts_dataset_is_content_addressed_and_preserves_roll_provenance() -> None:
    h1 = _segments(START_36M)
    d1 = _segments(START_36M)
    oi = {
        START_36M.date(): Decimal("80000"),
        (END - timedelta(days=1)).date(): Decimal("100000"),
    }

    first = build_forts_manifest(
        root="SI",
        start_at=START_36M,
        end_at=END,
        h1_segments=h1,
        d1_segments=d1,
        daily_open_interest=oi,
        min_history_months=36,
    )
    second = build_forts_manifest(
        root="SI",
        start_at=START_36M,
        end_at=END,
        h1_segments=h1,
        d1_segments=d1,
        daily_open_interest=oi,
        min_history_months=36,
    )

    assert first.status == DATA_READY
    assert first.blockers == ()
    assert first.manifest.snapshot_id == second.manifest.snapshot_id
    assert first.manifest.content_sha256 == second.manifest.content_sha256
    assert first.manifest.dataset_name == "forts:SI:continuous"

    h1_rows = [
        row for row in first.manifest.rows if row.values.get("stream") == "continuous_h1"
    ]
    assert h1_rows
    assert {row.values["contract_id"] for row in h1_rows} == {"SiM6", "SiU6"}
    assert all(row.values.get("segment_valid_until") is not None for row in h1_rows)
    assert first.manifest.source_watermark["roll_boundaries_valid"] is True
    assert first.manifest.source_watermark["segment_count"] == 2


def test_forts_dataset_rejects_roll_crossing_rows_instead_of_synthesizing_them() -> None:
    roll = END - timedelta(days=30)
    bad = (
        FuturesSegment(
            contract_id="SiM6",
            valid_from=START_36M,
            valid_until=roll,
            bars=(_bar(roll, "90000"),),
        ),
        FuturesSegment(
            contract_id="SiU6",
            valid_from=roll,
            valid_until=END,
            bars=(_bar(roll, "91000"),),
        ),
    )

    built = build_forts_manifest(
        root="SI",
        start_at=START_36M,
        end_at=END,
        h1_segments=bad,
        d1_segments=_segments(START_36M),
        daily_open_interest={
            START_36M.date(): Decimal("80000"),
            (END - timedelta(days=1)).date(): Decimal("100000"),
        },
        min_history_months=36,
    )

    h1_rows = [
        row for row in built.manifest.rows if row.values.get("stream") == "continuous_h1"
    ]
    assert [row.values["contract_id"] for row in h1_rows] == ["SiU6"]
