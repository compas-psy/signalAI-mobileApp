from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.control.bybit_funnel import FunnelFact, record_funnel_fact
from app.control.dashboard import build_control_dashboard
from app.models import DatasetSnapshot


def test_bybit_dashboard_exposes_runtime_roles_scan_funnel_and_dataset_readiness(session) -> None:
    now = datetime(2026, 8, 29, 20, tzinfo=UTC)
    record_funnel_fact(
        session,
        FunnelFact("CRYPTO:PERP:BTCUSDT", "PUBLISHED", "ACTIVE", sequence=1),
        occurred_at=now - timedelta(minutes=30),
    )
    record_funnel_fact(
        session,
        FunnelFact(
            "CRYPTO:PERP:ETHUSDT",
            "LIQUIDITY_BLOCKED",
            "LIQUIDITY_UNTRADEABLE",
            sequence=1,
        ),
        occurred_at=now - timedelta(minutes=30),
    )
    session.add(
        DatasetSnapshot(
            dataset_name="bybit:BTCUSDT:multistream",
            dataset_version="bybit-multistream-v1",
            schema_version="1",
            snapshot_id="a" * 64,
            tradable_at=now - timedelta(hours=1),
            source_watermark={
                "symbol": "BTCUSDT",
                "readiness": "DATA_READY",
                "coverage": [
                    {"stream": "klines", "ready": True, "reason": "READY"},
                    {"stream": "open_interest", "ready": True, "reason": "READY"},
                ],
            },
            row_count=100,
            content_sha256="b" * 64,
            manifest_sha256="a" * 64,
            artifact_key="bybit/test.json",
        )
    )
    session.flush()

    dashboard = build_control_dashboard(
        session,
        venue="BYBIT",
        window_hours=24,
        now=now,
    )

    assert dashboard["runtime_roles"]["live_generator"]["publishes_trade_ideas"] is True
    assert dashboard["runtime_roles"]["governance_controls_runtime"] is False
    assert dashboard["funnel"]["scan"]["universe"] == 2
    assert dashboard["funnel"]["scan"]["published"] == 1
    assert dashboard["data_readiness"]["status"] == "DATA_READY"
    assert dashboard["data_readiness"]["symbols"][0]["symbol"] == "BTCUSDT"
    assert dashboard["data_readiness"]["symbols"][0]["snapshot_id"] == "a" * 64
