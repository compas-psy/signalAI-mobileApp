from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.execution.enums import ExecutionKillSwitchLevel, ExecutionLifecycleMode
from app.execution.kill_switch import (
    clear_execution_kill_switch,
    get_execution_kill_switch_level,
    set_execution_kill_switch,
)
from app.execution.mode import ModeChangeAuthorization, change_execution_mode, get_execution_mode
from app.execution.venues.lighter_replay import (
    mark_lighter_nonce_submitting,
    reserve_lighter_nonce,
    resolve_lighter_order_identity,
)
from app.models.lighter_execution import LighterNonceReservation, LighterOrderActionBinding


OBSERVED_AT = datetime(2026, 8, 22, 20, 40, tzinfo=UTC)


def _set_mode(session, target: ExecutionLifecycleMode) -> None:
    current = get_execution_mode(session).mode
    if current == target:
        return
    change_execution_mode(
        session,
        target=target,
        actor="test",
        reason="reconciliation safety setup",
        authorization=ModeChangeAuthorization(
            allowed=True,
            actor="test-guard",
            reason="test setup authorization",
            detail_json={"test_only": True},
        ),
    )
    session.flush()


def _seed_submitting_action(
    session,
    *,
    client_order_id: str,
    nonce: int,
    action_type: str = "CREATE",
):
    identity = resolve_lighter_order_identity(
        session,
        account_index=42,
        client_order_id=client_order_id,
    )
    action_key = f"{action_type}:{client_order_id}"
    session.add(
        LighterOrderActionBinding(
            action_key=action_key,
            action_type=action_type,
            account_index=42,
            api_key_index=3,
            client_order_id=client_order_id,
            client_order_index=identity.client_order_index,
            market_index=0,
            request_hash="a" * 64,
        )
    )
    reserve_lighter_nonce(
        session,
        account_index=42,
        api_key_index=3,
        replay_key=action_key,
        provider_next_nonce=nonce,
    )
    mark_lighter_nonce_submitting(session, replay_key=action_key)
    session.commit()
    return action_key, identity.client_order_index


def _snapshot(*, nonce: int, provider_next_nonce: int):
    from app.execution.venues.lighter_reconciliation import LighterReconciliationSnapshot

    return LighterReconciliationSnapshot(
        account_index=42,
        api_key_index=3,
        provider_next_nonce=provider_next_nonce,
        observed_at=OBSERVED_AT,
    )


def test_fresh_ambiguous_submitting_action_halts_canary_before_return(session) -> None:
    from app.execution.venues.lighter_reconciliation import (
        reconcile_lighter_action_with_automatic_safety,
    )

    action_key, _ = _seed_submitting_action(session, client_order_id="safe-ambiguous", nonce=800)
    _set_mode(session, ExecutionLifecycleMode.CANARY)

    result = reconcile_lighter_action_with_automatic_safety(
        session,
        action_key=action_key,
        snapshot=_snapshot(nonce=800, provider_next_nonce=800),
    )

    assert result.reconciliation.outcome == "AMBIGUOUS"
    assert result.evidence_created is True
    assert result.automatic_halt_applied is True
    assert result.automatic_safety_trigger == "NONCE_RECONCILIATION_AMBIGUITY"
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.HALT_NEW_ENTRIES
    assert get_execution_mode(session).mode is ExecutionLifecycleMode.CANARY
    reservation = session.scalar(
        select(LighterNonceReservation).where(LighterNonceReservation.replay_key == action_key)
    )
    assert reservation is not None and reservation.state == "SUBMITTING"


def test_consumed_unknown_after_submitting_halts_canary_and_retires_nonce(session) -> None:
    from app.execution.venues.lighter_reconciliation import (
        reconcile_lighter_action_with_automatic_safety,
    )

    action_key, _ = _seed_submitting_action(session, client_order_id="safe-consumed", nonce=810)
    _set_mode(session, ExecutionLifecycleMode.CANARY)

    result = reconcile_lighter_action_with_automatic_safety(
        session,
        action_key=action_key,
        snapshot=_snapshot(nonce=810, provider_next_nonce=811),
    )

    assert result.reconciliation.outcome == "CONSUMED_UNKNOWN"
    assert result.evidence_created is True
    assert result.automatic_halt_applied is True
    assert result.automatic_safety_trigger == "NONCE_RECONCILIATION_AMBIGUITY"
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.HALT_NEW_ENTRIES
    reservation = session.scalar(
        select(LighterNonceReservation).where(LighterNonceReservation.replay_key == action_key)
    )
    assert reservation is not None and reservation.state == "CONSUMED"


def test_exact_order_resolution_does_not_halt_canary(session) -> None:
    from app.execution.venues.lighter_reconciliation import (
        LighterProviderOrderFact,
        LighterReconciliationSnapshot,
        reconcile_lighter_action_with_automatic_safety,
    )

    action_key, client_order_index = _seed_submitting_action(
        session, client_order_id="safe-resolved", nonce=820
    )
    _set_mode(session, ExecutionLifecycleMode.CANARY)
    result = reconcile_lighter_action_with_automatic_safety(
        session,
        action_key=action_key,
        snapshot=LighterReconciliationSnapshot(
            account_index=42,
            api_key_index=3,
            provider_next_nonce=821,
            observed_at=OBSERVED_AT,
            order=LighterProviderOrderFact(
                owner_account_index=42,
                market_index=0,
                client_order_index=client_order_index,
                nonce=820,
                order_id="order-safe-820",
                status="open",
                updated_at=OBSERVED_AT,
            ),
        ),
    )

    assert result.reconciliation.outcome == "ORDER_FOUND"
    assert result.evidence_created is True
    assert result.automatic_halt_applied is False
    assert result.automatic_safety_trigger is None
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.CLEAR


def test_replayed_ambiguity_after_owner_clear_cannot_rehalt(session) -> None:
    from app.execution.venues.lighter_reconciliation import (
        reconcile_lighter_action_with_automatic_safety,
    )

    action_key, _ = _seed_submitting_action(session, client_order_id="safe-replay", nonce=830)
    _set_mode(session, ExecutionLifecycleMode.CANARY)
    snapshot = _snapshot(nonce=830, provider_next_nonce=830)

    first = reconcile_lighter_action_with_automatic_safety(
        session, action_key=action_key, snapshot=snapshot
    )
    assert first.automatic_halt_applied is True
    clear_execution_kill_switch(
        session,
        actor="owner",
        reason="owner recovered after reviewing ambiguity",
    )

    replay = reconcile_lighter_action_with_automatic_safety(
        session, action_key=action_key, snapshot=snapshot
    )
    assert replay.reconciliation.outcome == "AMBIGUOUS"
    assert replay.evidence_created is False
    assert replay.automatic_halt_applied is False
    assert replay.automatic_safety_trigger is None
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.CLEAR


def test_non_canary_ambiguity_and_stronger_owner_switch_are_never_weakened(session) -> None:
    from app.execution.venues.lighter_reconciliation import (
        reconcile_lighter_action_with_automatic_safety,
    )

    action_key, _ = _seed_submitting_action(session, client_order_id="safe-sandbox", nonce=840)
    _set_mode(session, ExecutionLifecycleMode.SANDBOX)
    sandbox = reconcile_lighter_action_with_automatic_safety(
        session,
        action_key=action_key,
        snapshot=_snapshot(nonce=840, provider_next_nonce=840),
    )
    assert sandbox.reconciliation.outcome == "AMBIGUOUS"
    assert sandbox.automatic_halt_applied is False
    assert sandbox.automatic_safety_trigger is None
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.CLEAR

    _set_mode(session, ExecutionLifecycleMode.CANARY)
    set_execution_kill_switch(
        session,
        level=ExecutionKillSwitchLevel.CANCEL_PENDING_ENTRIES,
        actor="owner",
        reason="stronger owner safety action",
    )
    second_key, _ = _seed_submitting_action(session, client_order_id="safe-stronger", nonce=850)
    stronger = reconcile_lighter_action_with_automatic_safety(
        session,
        action_key=second_key,
        snapshot=_snapshot(nonce=850, provider_next_nonce=850),
    )
    assert stronger.reconciliation.outcome == "AMBIGUOUS"
    assert stronger.evidence_created is True
    assert stronger.automatic_halt_applied is False
    assert stronger.automatic_safety_trigger == "NONCE_RECONCILIATION_AMBIGUITY"
    assert get_execution_kill_switch_level(session) is ExecutionKillSwitchLevel.CANCEL_PENDING_ENTRIES
