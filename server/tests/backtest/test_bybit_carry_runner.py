from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.bybit_carry_runner import resolve_carry_facts
from app.datasets.snapshots import DatasetRow, ResolvedDataset


def test_resolve_carry_facts_builds_pit_mark_index_and_funding_history() -> None:
    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    rows: list[DatasetRow] = []
    for index in range(1, 10):
        at = start + timedelta(hours=8 * index)
        rows.append(
            DatasetRow(
                key=f"funding|{at.isoformat()}",
                tradable_at=at,
                values={"stream": "funding", "funding_rate": Decimal("0.002")},
            )
        )
        rows.append(
            DatasetRow(
                key=f"mark_price|{at.isoformat()}",
                tradable_at=at,
                values={"stream": "mark_price", "close": Decimal("100")},
            )
        )
        rows.append(
            DatasetRow(
                key=f"index_price|{at.isoformat()}",
                tradable_at=at,
                values={"stream": "index_price", "close": Decimal("100")},
            )
        )

    dataset = ResolvedDataset(
        dataset_name="bybit:BTCUSDT:multistream",
        dataset_version="bybit-multistream-v1",
        schema_version="1",
        snapshot_id="snapshot-1",
        tradable_at=start + timedelta(hours=8 * 9),
        source_watermark={"symbol": "BTCUSDT", "readiness": "DATA_READY"},
        row_count=len(rows),
        content_sha256="content",
        manifest_sha256="manifest",
        rows=tuple(rows),
    )

    facts = resolve_carry_facts(dataset)

    assert len(facts) == 9
    assert facts[-1].instrument_id == "BTCUSDT"
    assert facts[-1].funding_interval_minutes == 480
    assert facts[-1].current_funding_rate == Decimal("0.002")
    assert len(facts[-1].funding_history) == 9
    assert facts[-1].mark_price == Decimal("100")
    assert facts[-1].index_price == Decimal("100")
