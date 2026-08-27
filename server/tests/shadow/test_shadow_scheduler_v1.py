from __future__ import annotations

from datetime import timedelta

from app.scheduler.runner import build_default_scheduler


def test_default_scheduler_contains_separate_shadow_job_after_owner_scan() -> None:
    scheduler = build_default_scheduler(
        shadow_every=timedelta(minutes=15),
        shadow_runner=lambda _session: "shadow fixture",
    )

    names = [job.name for job in scheduler.jobs]
    assert "shadow" in names
    assert names.index("ingest") < names.index("scan") < names.index("shadow")


def test_shadow_scheduler_job_is_independent_from_owner_scan_callable() -> None:
    calls: list[str] = []

    def shadow_runner(_session) -> str:
        calls.append("shadow")
        return "shadow fixture"

    scheduler = build_default_scheduler(
        shadow_runner=shadow_runner,
        shadow_every=timedelta(minutes=15),
    )
    shadow = next(job for job in scheduler.jobs if job.name == "shadow")

    # The closure is a dedicated measurement job. Calling it cannot invoke
    # pipeline.scan or owner notification/lifecycle code as a side effect.
    assert shadow.run(None) == "shadow fixture"  # type: ignore[arg-type]
    assert calls == ["shadow"]
