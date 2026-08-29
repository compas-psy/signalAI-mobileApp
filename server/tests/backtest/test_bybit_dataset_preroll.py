from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import app.backtest.bybit_dataset as dataset
from app.backtest.bybit_history import HistoricalObservation


def test_sparse_funding_reader_gets_full_day_preroll(monkeypatch) -> None:
    start = datetime(2023, 8, 30, 21, tzinfo=UTC)
    end = datetime(2026, 8, 30, 21, tzinfo=UTC)
    seen = {}

    def funding(symbol, *, start_at, end_at, fetch):
        seen["symbol"] = symbol
        seen["start_at"] = start_at
        return (
            (
                HistoricalObservation(
                    observed_at=start - timedelta(hours=8),
                    tradable_at=start - timedelta(hours=8),
                    values={"funding_rate": Decimal("0.0001")},
                ),
                HistoricalObservation(
                    observed_at=end - timedelta(hours=8),
                    tradable_at=end - timedelta(hours=8),
                    values={"funding_rate": Decimal("0.0001")},
                ),
            ),
            (),
        )

    monkeypatch.setattr(dataset, "historical_funding", funding)

    collected = dataset.collect_multistream(
        "BTCUSDT",
        start_at=start,
        end_at=end,
        min_history_months=36,
        required_streams=("funding",),
        fetch=lambda _url: ({}, object()),
    )

    assert seen == {
        "symbol": "BTCUSDT",
        "start_at": start - timedelta(days=1),
    }
    assert collected.status == dataset.DATA_READY
    assert collected.built.coverage[0].reason == "READY"
