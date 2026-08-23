from __future__ import annotations

from sqlalchemy import select

from app.models import ExecutionModeActivationRequest
from tests.execution.test_canary_correlation_audit import (
    _evidence,
    _execution_chain,
    _snapshot,
)


def test_correlation_rejects_tampered_activation_account_scope(session, instrument) -> None:
    from app.execution.canary_correlation import build_canary_correlation_report

    snapshot, refs = _snapshot(session, instrument_id=instrument.instrument_id)
    _evidence(session, snapshot, refs)
    _execution_chain(session, instrument, snapshot)

    request = session.execute(
        select(ExecutionModeActivationRequest).where(
            ExecutionModeActivationRequest.preview_hash == snapshot.snapshot_hash
        )
    ).scalar_one()
    request.account = "999"
    session.flush()

    report = build_canary_correlation_report(
        session,
        snapshot_hash=snapshot.snapshot_hash,
    )

    assert report.status == "INCOMPLETE"
    assert report.activation_request_id is None
    assert "ACTIVATION_REQUEST_BINDING_MISSING" in report.blockers
    assert "CANARY_MODE_EVENT_BINDING_MISSING" in report.blockers
    assert "CANARY_EXECUTION_EVIDENCE_MISSING" in report.blockers
