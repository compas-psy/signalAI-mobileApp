from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.control.bybit_funnel import FunnelFact, record_funnel_fact
from app.control.dashboard import build_control_dashboard
from app.models import BacktestRun, DatasetSnapshot


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


def _backtest(
    *,
    strategy: str,
    created_at: datetime,
    trades: int,
    gate_passed: bool,
    reason: str | None = None,
    expectancy_r: Decimal | None = None,
) -> BacktestRun:
    return BacktestRun(
        label=f"bybit-entry-backtest:{strategy}:{created_at.timestamp()}",
        strategy=strategy,
        period_from=created_at.date() - timedelta(days=365),
        period_to=created_at.date(),
        config_hash="c" * 64,
        engine_version="test",
        universe_json=["CRYPTO", "BYBIT", "BTCUSDT"],
        trades=trades,
        net_return=None,
        profit_factor=Decimal("1.4") if trades else None,
        expectancy_r=expectancy_r,
        max_drawdown=Decimal("4.2") if trades else None,
        sharpe=Decimal("0.7") if trades else None,
        sortino=None,
        calmar=None,
        brier_score=None,
        pbo=None,
        top5_contribution=Decimal("0.2") if trades else None,
        report_json={
            "metric_space": "R_MULTIPLES",
            "outcome_metric": "paper_directional_alpha_r_v1",
        },
        gate_passed=gate_passed,
        gate_detail_json={} if reason is None else {"reason": reason},
        created_at=created_at,
    )


def test_bybit_dashboard_keeps_latest_backtest_per_strategy_visible(session) -> None:
    now = datetime(2026, 8, 30, 8, tzinfo=UTC)
    session.add_all(
        [
            _backtest(
                strategy="momentum_v2",
                created_at=now - timedelta(hours=3),
                trades=240,
                gate_passed=True,
                expectancy_r=Decimal("0.18"),
            ),
            _backtest(
                strategy="momentum_v2",
                created_at=now - timedelta(days=1),
                trades=200,
                gate_passed=False,
                expectancy_r=Decimal("0.05"),
            ),
            _backtest(
                strategy="breakout_v2",
                created_at=now - timedelta(hours=2),
                trades=0,
                gate_passed=False,
                reason="HISTORICAL_SPREAD_UNAVAILABLE",
            ),
            _backtest(
                strategy="crypto_carry_v1",
                created_at=now - timedelta(hours=1),
                trades=0,
                gate_passed=False,
                reason="CARRY_SETTLED_FUNDING_OUTCOME_UNAVAILABLE",
            ),
        ]
    )
    session.flush()

    dashboard = build_control_dashboard(session, venue="BYBIT", now=now)
    rows = {row["strategy"]: row for row in dashboard["backtest"]["by_strategy"]}

    assert set(rows) == {"momentum_v2", "breakout_v2", "crypto_carry_v1"}
    assert rows["momentum_v2"]["trades"] == 240
    assert rows["momentum_v2"]["expectancy_r"] == 0.18
    assert rows["momentum_v2"]["gate_passed"] is True
    assert (
        rows["breakout_v2"]["gate_detail"]["reason"]
        == "HISTORICAL_SPREAD_UNAVAILABLE"
    )
    assert (
        rows["crypto_carry_v1"]["gate_detail"]["reason"]
        == "CARRY_SETTLED_FUNDING_OUTCOME_UNAVAILABLE"
    )
    # Backward-compatible latest still points to the newest venue run.
    assert dashboard["backtest"]["latest"]["strategy"] == "crypto_carry_v1"
