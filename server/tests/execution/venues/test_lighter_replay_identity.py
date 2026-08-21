from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select


CONSUMED_AT = datetime(2026, 8, 21, 13, 0, tzinfo=UTC)


def test_client_order_index_is_stable_positive_signed_int64() -> None:
    from app.execution.venues.lighter_replay import derive_lighter_client_order_index

    first = derive_lighter_client_order_index("e-0123456789abcdef0123456789abcdef")
    replay = derive_lighter_client_order_index("e-0123456789abcdef0123456789abcdef")
    other = derive_lighter_client_order_index("x-0123456789abcdef0123456789abcdef")

    assert first == replay
    assert first != other
    assert 1 <= first <= (2**63 - 1)


def test_order_identity_is_persisted_and_bound_to_account(session) -> None:
    from app.execution.venues.lighter_replay import (
        LighterReplayError,
        resolve_lighter_order_identity,
    )
    from app.models.lighter_execution import LighterOrderIdentity

    first = resolve_lighter_order_identity(
        session,
        account_index=42,
        client_order_id="e-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    replay = resolve_lighter_order_identity(
        session,
        account_index=42,
        client_order_id="e-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )

    assert first.id == replay.id
    assert first.client_order_index == replay.client_order_index
    assert session.scalar(select(func.count()).select_from(LighterOrderIdentity)) == 1

    with pytest.raises(LighterReplayError, match="account"):
        resolve_lighter_order_identity(
            session,
            account_index=43,
            client_order_id="e-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )


def test_client_order_index_collision_fails_closed(session, monkeypatch) -> None:
    from app.execution.venues import lighter_replay

    monkeypatch.setattr(lighter_replay, "derive_lighter_client_order_index", lambda _: 77)

    first = lighter_replay.resolve_lighter_order_identity(
        session,
        account_index=7,
        client_order_id="e-first",
    )
    assert first.client_order_index == 77

    with pytest.raises(lighter_replay.LighterReplayError, match="collision"):
        lighter_replay.resolve_lighter_order_identity(
            session,
            account_index=7,
            client_order_id="e-second",
        )


def test_same_replay_key_reuses_exact_nonce_across_retry(session) -> None:
    from app.execution.venues.lighter_replay import reserve_lighter_nonce
    from app.models.lighter_execution import LighterNonceReservation

    first = reserve_lighter_nonce(
        session,
        account_index=42,
        api_key_index=3,
        replay_key="CREATE:e-abc",
        provider_next_nonce=120,
    )
    replay = reserve_lighter_nonce(
        session,
        account_index=42,
        api_key_index=3,
        replay_key="CREATE:e-abc",
        provider_next_nonce=999,
    )

    assert first.id == replay.id
    assert replay.nonce == 120
    assert replay.state == "RESERVED"
    assert session.scalar(select(func.count()).select_from(LighterNonceReservation)) == 1


def test_unresolved_nonce_blocks_different_transaction_until_consumed(session) -> None:
    from app.execution.venues.lighter_replay import (
        LighterNonceBusy,
        mark_lighter_nonce_consumed,
        reserve_lighter_nonce,
    )

    first = reserve_lighter_nonce(
        session,
        account_index=42,
        api_key_index=3,
        replay_key="CREATE:e-first",
        provider_next_nonce=120,
    )

    with pytest.raises(LighterNonceBusy, match="CREATE:e-first"):
        reserve_lighter_nonce(
            session,
            account_index=42,
            api_key_index=3,
            replay_key="CANCEL:e-second",
            provider_next_nonce=121,
        )

    consumed = mark_lighter_nonce_consumed(
        session,
        replay_key="CREATE:e-first",
        consumed_at=CONSUMED_AT,
    )
    assert consumed.state == "CONSUMED"
    assert consumed.consumed_at == CONSUMED_AT

    next_tx = reserve_lighter_nonce(
        session,
        account_index=42,
        api_key_index=3,
        replay_key="CANCEL:e-second",
        provider_next_nonce=121,
    )
    assert next_tx.nonce == 121


def test_provider_nonce_must_not_move_behind_consumed_local_evidence(session) -> None:
    from app.execution.venues.lighter_replay import (
        LighterNonceStateMismatch,
        mark_lighter_nonce_consumed,
        reserve_lighter_nonce,
    )

    reservation = reserve_lighter_nonce(
        session,
        account_index=9,
        api_key_index=253,
        replay_key="CREATE:e-one",
        provider_next_nonce=500,
    )
    mark_lighter_nonce_consumed(
        session,
        replay_key=reservation.replay_key,
        consumed_at=CONSUMED_AT,
    )

    with pytest.raises(LighterNonceStateMismatch, match="provider_next_nonce"):
        reserve_lighter_nonce(
            session,
            account_index=9,
            api_key_index=253,
            replay_key="CREATE:e-two",
            provider_next_nonce=500,
        )

    jumped = reserve_lighter_nonce(
        session,
        account_index=9,
        api_key_index=253,
        replay_key="CREATE:e-two",
        provider_next_nonce=503,
    )
    assert jumped.nonce == 503


def test_replay_key_is_bound_to_account_and_api_key(session) -> None:
    from app.execution.venues.lighter_replay import LighterReplayError, reserve_lighter_nonce

    reserve_lighter_nonce(
        session,
        account_index=1,
        api_key_index=2,
        replay_key="CREATE:immutable",
        provider_next_nonce=10,
    )

    with pytest.raises(LighterReplayError, match="scope"):
        reserve_lighter_nonce(
            session,
            account_index=1,
            api_key_index=3,
            replay_key="CREATE:immutable",
            provider_next_nonce=10,
        )


def test_sai069_boundary_contains_no_lighter_sdk_or_order_transport() -> None:
    from app.execution.venues import lighter_replay

    source = open(lighter_replay.__file__, encoding="utf-8").read().lower()
    for forbidden in (
        "import lighter",
        "signerclient",
        "create_order(",
        "cancel_order(",
        "send_tx",
        "httpx",
        "requests",
        "urlopen",
    ):
        assert forbidden not in source
