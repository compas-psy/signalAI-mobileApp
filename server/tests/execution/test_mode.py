from __future__ import annotations

import importlib

import pytest

from app.execution.enums import ExecutionLifecycleMode
from app.models import ExecutionModeEvent, ExecutionModeState, RiskState
from app.models.enums import ExecutionMode


def _mode_module():
    return importlib.import_module("app.execution.mode")


def test_server_mode_defaults_to_paper_and_materializes_in_db(session):
    mode = _mode_module()

    snapshot = mode.get_execution_mode(session)

    assert snapshot.mode == ExecutionLifecycleMode.PAPER
    row = session.get(ExecutionModeState, 1)
    assert row is not None
    assert row.mode == ExecutionLifecycleMode.PAPER


def test_execution_mode_state_is_authoritative_over_legacy_risk_state(session):
    mode = _mode_module()
    session.add(
        RiskState(
            id=1,
            execution_mode=ExecutionMode.LIVE_AUTO,
            kill_switch=False,
            kill_switch_reason="",
        )
    )
    session.flush()

    snapshot = mode.get_execution_mode(session)

    assert snapshot.mode == ExecutionLifecycleMode.PAPER


def test_preview_fails_closed_for_risk_increasing_transition_before_sai_031(session):
    mode = _mode_module()

    preview = mode.preview_execution_mode(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
    )

    assert preview.current == ExecutionLifecycleMode.PAPER
    assert preview.target == ExecutionLifecycleMode.SANDBOX
    assert preview.allowed is False
    assert "promotion guard" in " ".join(preview.blockers).lower()


def test_same_mode_preview_and_change_are_idempotent_without_event(session):
    mode = _mode_module()

    preview = mode.preview_execution_mode(
        session,
        target=ExecutionLifecycleMode.PAPER,
    )
    result = mode.change_execution_mode(
        session,
        target=ExecutionLifecycleMode.PAPER,
        actor="owner",
        reason="repeat request",
    )

    assert preview.allowed is True
    assert preview.blockers == ()
    assert result.mode == ExecutionLifecycleMode.PAPER
    assert session.query(ExecutionModeEvent).count() == 0


def test_unguarded_mode_change_is_rejected_and_does_not_mutate_db(session):
    mode = _mode_module()

    with pytest.raises(mode.ExecutionModeChangeRejected, match="promotion guard"):
        mode.change_execution_mode(
            session,
            target=ExecutionLifecycleMode.SANDBOX,
            actor="owner",
            reason="try sandbox",
        )

    assert mode.get_execution_mode(session).mode == ExecutionLifecycleMode.PAPER
    assert session.query(ExecutionModeEvent).count() == 0


def test_guard_authorization_is_the_only_internal_path_that_changes_mode(session):
    mode = _mode_module()
    authorization = mode.ModeChangeAuthorization(
        allowed=True,
        actor="promotion-guard",
        reason="technical sandbox readiness verified",
        detail_json={"gate": "SAI-031-test-double"},
    )

    result = mode.change_execution_mode(
        session,
        target=ExecutionLifecycleMode.SANDBOX,
        actor="owner",
        reason="owner requested sandbox",
        authorization=authorization,
    )
    session.flush()

    assert result.mode == ExecutionLifecycleMode.SANDBOX
    row = session.get(ExecutionModeState, 1)
    assert row is not None
    assert row.mode == ExecutionLifecycleMode.SANDBOX
    event = session.query(ExecutionModeEvent).one()
    assert event.from_mode == ExecutionLifecycleMode.PAPER
    assert event.to_mode == ExecutionLifecycleMode.SANDBOX
    assert event.actor == "owner"
    assert event.reason == "owner requested sandbox"
    assert event.detail_json["authorization_actor"] == "promotion-guard"
    assert event.detail_json["authorization_reason"] == (
        "technical sandbox readiness verified"
    )


def test_mode_api_exists_and_remains_fail_closed_without_promotion_guard(session):
    api = importlib.import_module("app.api.v1.execution")

    current = api.get_execution_mode(db=session)
    preview = api.preview_execution_mode(
        request=api.ExecutionModePreviewRequest(
            target=ExecutionLifecycleMode.SANDBOX,
        ),
        db=session,
    )

    assert current.mode == ExecutionLifecycleMode.PAPER
    assert preview.allowed is False
    with pytest.raises(Exception) as exc_info:
        api.change_execution_mode(
            request=api.ExecutionModeChangeRequest(
                target=ExecutionLifecycleMode.SANDBOX,
                reason="owner requested sandbox",
            ),
            db=session,
        )
    assert getattr(exc_info.value, "status_code", None) == 409
    assert session.query(ExecutionModeEvent).count() == 0
