from datetime import timedelta

from app.scheduler.runner import build_default_scheduler


def test_paper_ab_job_runs_after_shadow_before_owner_scan_without_side_effect_calls() -> None:
    calls: list[str] = []

    def shadow(_session):
        calls.append("shadow")
        return "shadow"

    def paper_ab(_session):
        calls.append("paper_ab")
        return "paper_ab"

    scheduler = build_default_scheduler(
        shadow_every=timedelta(minutes=15),
        shadow_runner=shadow,
        paper_ab_every=timedelta(minutes=15),
        paper_ab_runner=paper_ab,
    )
    names = [job.name for job in scheduler.jobs]

    assert names.index("ingest") < names.index("shadow")
    assert names.index("shadow") < names.index("paper_ab") < names.index("scan")
    assert names.count("paper_ab") == 1
    assert calls == []
