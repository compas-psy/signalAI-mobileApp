from __future__ import annotations

from tests.execution.test_canary_correlation_audit import (
    _evidence,
    _execution_chain,
    _snapshot as _correlation_snapshot,
)


def test_correlation_rejects_mode_event_missing_full_owner_scope(session, instrument) -> None:
    from sqlalchemy import select

    from app.execution.canary_correlation import build_canary_correlation_report
    from app.models import ExecutionModeEvent

    snapshot, refs = _correlation_snapshot(session, instrument_id=instrument.instrument_id)
    _evidence(session, snapshot, refs)
    _execution_chain(session, instrument, snapshot, full_owner_scope=False)

    event = session.execute(
        select(ExecutionModeEvent).where(
            ExecutionModeEvent.detail_json["canary_policy_snapshot_hash"].astext
            == snapshot.snapshot_hash
        )
    ).scalar_one()
    report = build_canary_correlation_report(
        session,
        snapshot_hash=snapshot.snapshot_hash,
    )

    assert event.detail_json["canary_policy_snapshot_hash"] == snapshot.snapshot_hash
    assert report.status == "INCOMPLETE"
    assert "CANARY_MODE_EVENT_OWNER_SCOPE_INCOMPLETE" in report.blockers
