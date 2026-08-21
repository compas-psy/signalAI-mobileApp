from pathlib import Path


PRODUCTION_JOB_ORDER = (
    "universe",
    "capital",
    "ingest",
    "review",
    "supervise",
    "trigger",
    "paper-live",
    "paper",
    "shadow",
    "paper_ab",
    "scan",
    "equity-warmup",
    "equity-ranking",
    "portfolio",
    "research",
)


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_market_lane_cannot_be_starved_by_portfolio_or_research() -> None:
    from app.scheduler.lanes import SchedulerLane, select_job_names

    selected = select_job_names(PRODUCTION_JOB_ORDER, SchedulerLane.MARKET)

    assert "portfolio" not in selected
    assert "research" not in selected
    for critical in ("paper-live", "ingest", "shadow", "paper_ab", "scan"):
        assert critical in selected
    assert selected.index("ingest") < selected.index("shadow")
    assert selected.index("shadow") < selected.index("paper_ab")
    assert selected.index("paper_ab") < selected.index("scan")


def test_heavy_lane_contains_only_long_running_non_trading_jobs() -> None:
    from app.scheduler.lanes import SchedulerLane, select_job_names

    assert select_job_names(PRODUCTION_JOB_ORDER, SchedulerLane.HEAVY) == (
        "portfolio",
        "research",
    )


def test_all_lane_remains_available_for_tests_and_local_compatibility() -> None:
    from app.scheduler.lanes import SchedulerLane, select_job_names

    assert select_job_names(PRODUCTION_JOB_ORDER, SchedulerLane.ALL) == PRODUCTION_JOB_ORDER


def test_production_compose_runs_market_and_heavy_lanes_as_separate_processes() -> None:
    compose = (_root() / "server/docker-compose.yml").read_text()

    assert "  scheduler:" in compose
    assert "  scheduler-heavy:" in compose
    market = compose.split("  scheduler:", 1)[1].split("  scheduler-heavy:", 1)[0]
    heavy = compose.split("  scheduler-heavy:", 1)[1].split("\n  execution:", 1)[0]
    assert "SIGNALAI_SCHEDULER_LANE: market" in market
    assert "SIGNALAI_SCHEDULER_LANE: heavy" in heavy
    assert 'command: ["python", "-m", "app.scheduler"]' in market
    assert 'command: ["python", "-m", "app.scheduler"]' in heavy


def test_scheduler_entrypoint_applies_lane_before_running_forever() -> None:
    entrypoint = (_root() / "server/app/scheduler/__main__.py").read_text()

    assert "SIGNALAI_SCHEDULER_LANE" in entrypoint
    assert "apply_scheduler_lane" in entrypoint
    assert entrypoint.index("apply_scheduler_lane") < entrypoint.index("run_forever(")
