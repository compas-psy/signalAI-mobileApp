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


def test_market_lane_prioritizes_owner_scan_before_experimental_measurement() -> None:
    from app.scheduler.lanes import SchedulerLane, select_job_names

    selected = select_job_names(PRODUCTION_JOB_ORDER, SchedulerLane.MARKET)

    assert "portfolio" not in selected
    assert "research" not in selected
    for critical in ("paper-live", "ingest", "scan", "shadow", "paper_ab"):
        assert critical in selected
    assert selected.index("ingest") < selected.index("scan")
    assert selected.index("scan") < selected.index("shadow")
    assert selected.index("shadow") < selected.index("paper_ab")


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
    assert 'command: ["python", "-m", "app.scheduler.heavy"]' in heavy


def test_heavy_lane_reuses_the_same_explicit_scheduler_image() -> None:
    compose = (_root() / "server/docker-compose.yml").read_text()
    market = compose.split("  scheduler:", 1)[1].split("  scheduler-heavy:", 1)[0]
    heavy = compose.split("  scheduler-heavy:", 1)[1].split("\n  execution:", 1)[0]
    deploy = (_root() / ".github/workflows/deploy-release.yml").read_text()

    assert "image: signalai-scheduler" in market
    assert "image: signalai-scheduler" in heavy
    assert "build: ." in market
    assert "build:" not in heavy
    assert 'docker compose --env-file "$ENV_FILE" build api scheduler' in deploy


def test_market_runtime_filters_default_jobs_before_returning_scheduler() -> None:
    runtime = (_root() / "server/app/scheduler/p0_runtime.py").read_text()

    assert "SIGNALAI_SCHEDULER_LANE" in runtime
    assert "apply_scheduler_lane" in runtime
    assert runtime.index("_add_capital_job") < runtime.rindex("apply_scheduler_lane")


def test_heavy_entrypoint_has_no_market_startup_bootstrap() -> None:
    heavy = (_root() / "server/app/scheduler/heavy.py").read_text()

    assert "SchedulerLane.HEAVY" in heavy
    assert "run_forever(" in heavy
    assert "purge_premature_moex_bars" not in heavy
    assert "materialize(" not in heavy
    assert "paper_live" not in heavy


def test_runtime_logs_include_both_scheduler_lanes() -> None:
    workflow = (_root() / ".github/workflows/runtime-logs.yml").read_text()

    assert "$COMPOSE logs --since 30h --tail 600 scheduler scheduler-heavy" in workflow
