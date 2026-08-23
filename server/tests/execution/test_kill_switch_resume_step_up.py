from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_db
from app.execution.enums import ExecutionKillSwitchLevel
from app.execution.kill_switch import set_execution_kill_switch
from app.main import app
from app.models import AuditEvent
from app.models.risk import RiskState
from tests.conftest import DEVICE_HEADERS


_STEP_UP_BLOCKER = "EXECUTION_KILL_SWITCH_CLEAR_STEP_UP_REQUIRED"


def test_device_bearer_cannot_clear_kill_switch_without_owner_step_up(session) -> None:
    set_execution_kill_switch(
        session,
        level=ExecutionKillSwitchLevel.HALT_NEW_ENTRIES,
        actor="owner",
        reason="security hold",
    )
    before_off_count = len(
        session.execute(
            select(AuditEvent.id).where(AuditEvent.action == "kill_switch_off")
        ).scalars().all()
    )

    app.dependency_overrides[get_db] = lambda: session
    try:
        with TestClient(app, headers=DEVICE_HEADERS) as client:
            response = client.post(
                "/api/v1/risk/resume",
                json={"reason": "ordinary device bearer is not step-up"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"] == _STEP_UP_BLOCKER

    state = session.get(RiskState, 1, populate_existing=True)
    assert state is not None
    assert state.kill_switch is True
    assert state.kill_switch_level == ExecutionKillSwitchLevel.HALT_NEW_ENTRIES
    assert state.kill_switch_reason == "security hold"

    after_off_count = len(
        session.execute(
            select(AuditEvent.id).where(AuditEvent.action == "kill_switch_off")
        ).scalars().all()
    )
    assert after_off_count == before_off_count
