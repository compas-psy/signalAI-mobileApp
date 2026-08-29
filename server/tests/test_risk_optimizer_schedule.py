from __future__ import annotations

from datetime import timedelta

from app.scheduler.p0_runtime import build_default_scheduler


def test_risk_optimizer_is_scheduled_after_paper_measurement(session) -> None:
    calls: list[str] = []

    def risk_optimizer(_session) -> str:
        calls.append("risk")
        return "risk-ok"

    scheduler = build_default_scheduler(
        fetch=lambda _url: ({"retCode": 0, "result": {"list": []}}, object()),
        shadow_runner=lambda _session: "shadow-ok",
        paper_ab_runner=lambda _session: "paper-ok",
        risk_optimizer_runner=risk_optimizer,
        risk_optimizer_every=timedelta(hours=24),
    )

    names = [job.name for job in scheduler.jobs]
    assert "risk_optimizer" in names
    assert names.index("risk_optimizer") > names.index("paper_ab")
    job = next(job for job in scheduler.jobs if job.name == "risk_optimizer")
    assert job.every == timedelta(hours=24)
    assert job.run(session) == "risk-ok"
    assert calls == ["risk"]
