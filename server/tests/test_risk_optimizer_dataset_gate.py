from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.models import DatasetSnapshot
from app.risk.optimizer import _dataset_readiness_blockers


def _row(*, venue: str, symbol: str) -> dict[str, object]:
    return {
        "at": datetime(2026, 8, 20, tzinfo=UTC),
        "mode": "balanced",
        "actual": Decimal("0.4"),
        "mfe": Decimal("1.2"),
        "instrument_id": f"{'CRYPTO:PERP' if venue == 'CRYPTO' else 'MOEX:FUT'}:{symbol}",
        "venue": venue,
        "symbol": symbol,
    }


def _snapshot(symbol: str, readiness: str) -> DatasetSnapshot:
    return DatasetSnapshot(
        dataset_name=f"bybit:{symbol}:multistream",
        dataset_version="bybit_multistream_v1",
        schema_version="bybit_multistream_v1",
        snapshot_id=("a" if readiness == "DATA_READY" else "b") * 64,
        tradable_at=datetime(2026, 8, 21, tzinfo=UTC),
        source_watermark={"readiness": readiness},
        row_count=10,
        content_sha256="c" * 64,
        manifest_sha256="d" * 64,
        artifact_key=f"datasets/bybit/{symbol}.json",
    )


def test_forts_only_optimizer_evidence_does_not_require_bybit_dataset(session) -> None:
    assert _dataset_readiness_blockers(session, [_row(venue="MOEX", symbol="RIU6")]) == ()


def test_crypto_optimizer_evidence_blocks_without_36m_snapshot(session) -> None:
    blockers = _dataset_readiness_blockers(
        session,
        [_row(venue="CRYPTO", symbol="BTCUSDT")],
    )
    assert blockers == ("DATASET_MISSING:BTCUSDT",)


def test_crypto_optimizer_evidence_blocks_on_data_blocked_snapshot(session) -> None:
    session.add(_snapshot("BTCUSDT", "DATA_BLOCKED"))
    session.flush()

    blockers = _dataset_readiness_blockers(
        session,
        [_row(venue="CRYPTO", symbol="BTCUSDT")],
    )
    assert blockers == ("DATA_BLOCKED:BTCUSDT",)


def test_crypto_optimizer_evidence_passes_with_data_ready_snapshot(session) -> None:
    session.add(_snapshot("BTCUSDT", "DATA_READY"))
    session.flush()

    assert _dataset_readiness_blockers(
        session,
        [_row(venue="CRYPTO", symbol="BTCUSDT")],
    ) == ()
