from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.execution.venues.lighter_replay import (
    reserve_lighter_nonce,
    resolve_lighter_order_identity,
)
from app.models.lighter_execution import (
    LighterNonceReservation,
    LighterOrderActionBinding,
)


OBSERVED_AT = datetime(2026, 8, 21, 16, 0, tzinfo=UTC)


def _seed_action(
    session,
    *,
    action_type: str = "CREATE",
    client_order_id: str = "e-reconcile",
    account_index: int = 42,
    api_key_index: int = 3,
    market_index: int = 0,
    nonce: int = 120,
):
    identity = resolve_lighter_order_identity(
        session,
        account_index=account_index,
        client_order_id=client_order_id,
    )
    action_key = f"{action_type}:{client_order_id}"
    session.add(
        LighterOrderActionBinding(
            action_key=action_key,
            action_type=action_type,
            account_index=account_index,
            api_key_index=api_key_index,
            client_order_id=client_order_id,
            client_order_index=identity.client_order_index,
            market_index=market_index,
            request_hash="a" * 64,
        )
    )
    reserve_lighter_nonce(
        session,
        account_index=account_index,
        api_key_index=api_key_index,
        replay_key=action_key,
        provider_next_nonce=nonce,
    )
    session.commit()
    return action_key, identity.client_order_index


def test_exact_provider_order_resolves_create_and_consumes_nonce(session) -> None:
    from app.execution.venues.lighter_reconciliation import (
        LighterProviderOrderFact,
        LighterReconciliationSnapshot,
        reconcile_lighter_action,
    )
    from app.models.lighter_execution import LighterReconciliationEvidence

    action_key, client_order_index = _seed_action(session)
    result = reconcile_lighter_action(
        session,
        action_key=action_key,
        snapshot=LighterReconciliationSnapshot(
            account_index=42,
            api_key_index=3,
            provider_next_nonce=121,
            observed_at=OBSERVED_AT,
            order=LighterProviderOrderFact(
                owner_account_index=42,
                market_index=0,
                client_order_index=client_order_index,
                nonce=120,
                order_id="order-123",
                status="open",
                updated_at=OBSERVED_AT,
            ),
        ),
    )

    assert result.outcome == "ORDER_FOUND"
    assert result.provider_order_id == "order-123"
    assert result.provider_status == "open"
    reservation = session.scalar(
        select(LighterNonceReservation).where(
            LighterNonceReservation.replay_key == action_key
        )
    )
    assert reservation is not None and reservation.state == "CONSUMED"
    assert session.scalar(select(func.count()).select_from(LighterReconciliationEvidence)) == 1


def test_exact_provider_transaction_resolves_cancel_without_inferring_from_order(session) -> None:
    from app.execution.venues.lighter_reconciliation import (
        LighterProviderOrderFact,
        LighterProviderTransactionFact,
        LighterReconciliationSnapshot,
        reconcile_lighter_action,
    )

    action_key, client_order_index = _seed_action(session, action_type="CANCEL")
    result = reconcile_lighter_action(
        session,
        action_key=action_key,
        snapshot=LighterReconciliationSnapshot(
            account_index=42,
            api_key_index=3,
            provider_next_nonce=121,
            observed_at=OBSERVED_AT,
            order=LighterProviderOrderFact(
                owner_account_index=42,
                market_index=0,
                client_order_index=client_order_index,
                nonce=119,
                order_id="entry-order",
                status="open",
                updated_at=OBSERVED_AT,
            ),
            transaction=LighterProviderTransactionFact(
                account_index=42,
                api_key_index=3,
                nonce=120,
                tx_hash="0xcancel",
                status=1,
                executed_at=OBSERVED_AT,
            ),
        ),
    )

    assert result.outcome == "TX_FOUND"
    assert result.provider_tx_hash == "0xcancel"
    assert result.provider_order_id is None


def test_same_nonce_without_provider_evidence_stays_ambiguous_and_reserved(session) -> None:
    from app.execution.venues.lighter_reconciliation import (
        LighterReconciliationSnapshot,
        reconcile_lighter_action,
    )

    action_key, _ = _seed_action(session, nonce=220)
    result = reconcile_lighter_action(
        session,
        action_key=action_key,
        snapshot=LighterReconciliationSnapshot(
            account_index=42,
            api_key_index=3,
            provider_next_nonce=220,
            observed_at=OBSERVED_AT,
        ),
    )

    assert result.outcome == "AMBIGUOUS"
    reservation = session.scalar(
        select(LighterNonceReservation).where(
            LighterNonceReservation.replay_key == action_key
        )
    )
    assert reservation is not None and reservation.state == "RESERVED"


def test_advanced_nonce_without_exact_evidence_becomes_consumed_unknown_never_absent(session) -> None:
    from app.execution.venues.lighter_reconciliation import (
        LighterReconciliationSnapshot,
        reconcile_lighter_action,
    )

    action_key, _ = _seed_action(session, nonce=300)
    result = reconcile_lighter_action(
        session,
        action_key=action_key,
        snapshot=LighterReconciliationSnapshot(
            account_index=42,
            api_key_index=3,
            provider_next_nonce=305,
            observed_at=OBSERVED_AT,
        ),
    )

    assert result.outcome == "CONSUMED_UNKNOWN"
    assert result.provider_order_id is None
    assert result.provider_status is None
    reservation = session.scalar(
        select(LighterNonceReservation).where(
            LighterNonceReservation.replay_key == action_key
        )
    )
    assert reservation is not None and reservation.state == "CONSUMED"


def test_cancel_order_presence_does_not_prove_cancel_transaction(session) -> None:
    from app.execution.venues.lighter_reconciliation import (
        LighterProviderOrderFact,
        LighterReconciliationSnapshot,
        reconcile_lighter_action,
    )

    action_key, client_order_index = _seed_action(
        session,
        action_type="CANCEL",
        nonce=400,
    )
    result = reconcile_lighter_action(
        session,
        action_key=action_key,
        snapshot=LighterReconciliationSnapshot(
            account_index=42,
            api_key_index=3,
            provider_next_nonce=401,
            observed_at=OBSERVED_AT,
            order=LighterProviderOrderFact(
                owner_account_index=42,
                market_index=0,
                client_order_index=client_order_index,
                nonce=399,
                order_id="still-present",
                status="open",
                updated_at=OBSERVED_AT,
            ),
        ),
    )

    assert result.outcome == "CONSUMED_UNKNOWN"
    assert result.provider_order_id is None


def test_provider_nonce_regression_fails_closed_and_keeps_reservation(session) -> None:
    from app.execution.venues.lighter_reconciliation import (
        LighterReconciliationSnapshot,
        LighterReconciliationStateError,
        reconcile_lighter_action,
    )

    action_key, _ = _seed_action(session, nonce=500)
    with pytest.raises(LighterReconciliationStateError, match="provider_next_nonce"):
        reconcile_lighter_action(
            session,
            action_key=action_key,
            snapshot=LighterReconciliationSnapshot(
                account_index=42,
                api_key_index=3,
                provider_next_nonce=499,
                observed_at=OBSERVED_AT,
            ),
        )

    session.rollback()
    reservation = session.scalar(
        select(LighterNonceReservation).where(
            LighterNonceReservation.replay_key == action_key
        )
    )
    assert reservation is not None and reservation.state == "RESERVED"


def test_mismatched_provider_order_fact_fails_closed_without_consuming_nonce(session) -> None:
    from app.execution.venues.lighter_reconciliation import (
        LighterProviderOrderFact,
        LighterReconciliationSnapshot,
        LighterReconciliationStateError,
        reconcile_lighter_action,
    )

    action_key, client_order_index = _seed_action(session, nonce=600)
    with pytest.raises(LighterReconciliationStateError, match="market"):
        reconcile_lighter_action(
            session,
            action_key=action_key,
            snapshot=LighterReconciliationSnapshot(
                account_index=42,
                api_key_index=3,
                provider_next_nonce=601,
                observed_at=OBSERVED_AT,
                order=LighterProviderOrderFact(
                    owner_account_index=42,
                    market_index=99,
                    client_order_index=client_order_index,
                    nonce=600,
                    order_id="wrong-market",
                    status="open",
                    updated_at=OBSERVED_AT,
                ),
            ),
        )

    session.rollback()
    reservation = session.scalar(
        select(LighterNonceReservation).where(
            LighterNonceReservation.replay_key == action_key
        )
    )
    assert reservation is not None and reservation.state == "RESERVED"


def test_identical_snapshot_replay_is_idempotent_and_boundary_has_no_provider_writes(session) -> None:
    from app.execution.venues import lighter_reconciliation
    from app.execution.venues.lighter_reconciliation import (
        LighterReconciliationSnapshot,
        reconcile_lighter_action,
    )
    from app.models.lighter_execution import LighterReconciliationEvidence

    action_key, _ = _seed_action(session, nonce=700)
    snapshot = LighterReconciliationSnapshot(
        account_index=42,
        api_key_index=3,
        provider_next_nonce=701,
        observed_at=OBSERVED_AT,
    )
    first = reconcile_lighter_action(session, action_key=action_key, snapshot=snapshot)
    replay = reconcile_lighter_action(session, action_key=action_key, snapshot=snapshot)

    assert first == replay
    assert first.outcome == "CONSUMED_UNKNOWN"
    assert session.scalar(select(func.count()).select_from(LighterReconciliationEvidence)) == 1

    source = open(lighter_reconciliation.__file__, encoding="utf-8").read().lower()
    for forbidden in (
        "create_order(",
        "cancel_order(",
        "send_tx",
        "submit(",
        "import lighter",
        "httpx",
        "requests",
        "urlopen",
    ):
        assert forbidden not in source
