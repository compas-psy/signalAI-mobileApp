from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.execution.venues.lighter_facts import LighterMarketFact


def _market() -> LighterMarketFact:
    from datetime import UTC, datetime

    return LighterMarketFact(
        market_id=0,
        symbol="ETH",
        status="active",
        min_base_amount=Decimal("0.001"),
        min_quote_amount=Decimal("5"),
        size_decimals=4,
        price_decimals=2,
        quote_decimals=2,
        maker_fee_pct=Decimal("0.0001"),
        taker_fee_pct=Decimal("0.0004"),
        liquidation_fee_pct=Decimal("0.005"),
        order_quote_limit=Decimal("1000000"),
        multiplier=Decimal("1"),
        observed_at=datetime(2026, 8, 22, 13, 0, tzinfo=UTC),
    )


def _sessions(session):
    return sessionmaker(bind=session.get_bind(), expire_on_commit=False)


class AmbiguousTransport:
    account_index = 42
    api_key_index = 3

    def __init__(self) -> None:
        self.next_nonce_calls = 0
        self.create_calls = 0

    def next_nonce(self) -> int:
        self.next_nonce_calls += 1
        return 77

    def create_order(self, **kwargs):
        self.create_calls += 1
        raise TimeoutError("provider outcome unknown")

    def cancel_order(self, **kwargs):
        raise AssertionError("unexpected cancel")


def test_submit_is_durably_marked_before_provider_io_and_timeout_cannot_blindly_retry(session) -> None:
    from app.execution.venues.lighter_actions import (
        LighterActionRequiresReconciliation,
        LighterOrderActions,
    )
    from app.models.lighter_execution import LighterNonceReservation

    sessions = _sessions(session)
    transport = AmbiguousTransport()
    actions = LighterOrderActions(session_factory=sessions, transport=transport)
    kwargs = dict(
        market=_market(),
        client_order_id="e-ambiguous",
        quantity=Decimal("0.02"),
        price=Decimal("3900"),
        is_ask=False,
        post_only=False,
    )

    with pytest.raises(TimeoutError, match="outcome unknown"):
        actions.create_limit(**kwargs)

    with sessions() as verifier:
        reservation = verifier.scalar(
            select(LighterNonceReservation).where(
                LighterNonceReservation.replay_key == "CREATE:e-ambiguous"
            )
        )
        assert reservation is not None
        assert reservation.state == "SUBMITTING"
        assert reservation.consumed_at is None

    with pytest.raises(LighterActionRequiresReconciliation, match="reconciliation"):
        actions.create_limit(**kwargs)

    assert transport.next_nonce_calls == 1
    assert transport.create_calls == 1


def test_reconciliation_can_retire_submitting_nonce_when_provider_evidence_proves_consumption(session) -> None:
    from datetime import UTC, datetime

    from app.execution.venues.lighter_actions import LighterOrderActions
    from app.execution.venues.lighter_reconciliation import (
        LighterProviderTransactionFact,
        LighterReconciliationSnapshot,
        reconcile_lighter_action,
    )
    from app.models.lighter_execution import LighterNonceReservation

    sessions = _sessions(session)
    transport = AmbiguousTransport()
    actions = LighterOrderActions(session_factory=sessions, transport=transport)

    with pytest.raises(TimeoutError):
        actions.create_limit(
            market=_market(),
            client_order_id="e-reconcile",
            quantity=Decimal("0.02"),
            price=Decimal("3900"),
            is_ask=False,
            post_only=False,
        )

    observed_at = datetime(2026, 8, 22, 13, 5, tzinfo=UTC)
    with sessions() as db:
        result = reconcile_lighter_action(
            db,
            action_key="CREATE:e-reconcile",
            snapshot=LighterReconciliationSnapshot(
                account_index=42,
                api_key_index=3,
                provider_next_nonce=78,
                observed_at=observed_at,
                transaction=LighterProviderTransactionFact(
                    account_index=42,
                    api_key_index=3,
                    nonce=77,
                    tx_hash="0xaccepted",
                    status=200,
                    executed_at=observed_at,
                ),
            ),
        )
        db.commit()
        assert result.outcome == "TX_FOUND"

    with sessions() as verifier:
        reservation = verifier.scalar(
            select(LighterNonceReservation).where(
                LighterNonceReservation.replay_key == "CREATE:e-reconcile"
            )
        )
        assert reservation is not None
        assert reservation.state == "CONSUMED"
        assert reservation.consumed_at == observed_at
