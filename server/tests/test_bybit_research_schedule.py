from __future__ import annotations

from datetime import timedelta

from app.scheduler.p0_runtime import build_default_scheduler


def _scheduler(monkeypatch, *, lane: str | None):
    if lane is None:
        monkeypatch.delenv("SIGNALAI_SCHEDULER_LANE", raising=False)
    else:
        monkeypatch.setenv("SIGNALAI_SCHEDULER_LANE", lane)
    return build_default_scheduler(
        fetch=lambda _url: ({"retCode": 0, "result": {"list": []}}, object()),
        shadow_runner=lambda _session: "shadow-ok",
        paper_ab_runner=lambda _session: "paper-ok",
        bybit_research_runner=lambda _session: "bybit-research-ok",
        bybit_backtest_runner=lambda _session: "bybit-backtest-ok",
        risk_optimizer_runner=lambda _session: "risk-ok",
        bybit_research_every=timedelta(hours=24),
        bybit_backtest_every=timedelta(hours=24),
        risk_optimizer_every=timedelta(hours=24),
    )


def test_bybit_research_and_backtest_run_before_risk_optimizer(monkeypatch) -> None:
    scheduler = _scheduler(monkeypatch, lane=None)
    names = [job.name for job in scheduler.jobs]

    assert "bybit_research" in names
    assert "bybit_backtest" in names
    assert "risk_optimizer" in names
    assert names.index("bybit_research") > names.index("paper_ab")
    assert names.index("bybit_backtest") > names.index("bybit_research")
    assert names.index("risk_optimizer") > names.index("bybit_backtest")

    research = next(job for job in scheduler.jobs if job.name == "bybit_research")
    backtest = next(job for job in scheduler.jobs if job.name == "bybit_backtest")
    assert research.every == timedelta(hours=24)
    assert backtest.every == timedelta(hours=24)
    assert research.run(object()) == "bybit-research-ok"
    assert backtest.run(object()) == "bybit-backtest-ok"


def test_market_lane_excludes_heavy_bybit_research_backtest_and_optimizer(monkeypatch) -> None:
    scheduler = _scheduler(monkeypatch, lane="market")
    names = [job.name for job in scheduler.jobs]

    assert "scan" in names
    assert "bybit_research" not in names
    assert "bybit_backtest" not in names
    assert "risk_optimizer" not in names


def test_heavy_lane_contains_research_then_bybit_backtest_then_optimizer(monkeypatch) -> None:
    scheduler = _scheduler(monkeypatch, lane="heavy")
    names = [job.name for job in scheduler.jobs]

    assert "scan" not in names
    assert "portfolio" in names
    assert "research" in names
    assert "bybit_research" in names
    assert "bybit_backtest" in names
    assert "risk_optimizer" in names
    assert names.index("bybit_research") < names.index("bybit_backtest")
    assert names.index("bybit_backtest") < names.index("risk_optimizer")
