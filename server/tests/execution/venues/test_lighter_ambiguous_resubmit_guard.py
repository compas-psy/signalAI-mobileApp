from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import sessionmaker

from app.execution.venues.lighter_facts import LighterMarketFact


def _market() -> LighterMarketFact:
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
        observed_at=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
    )


class AmbiguousTransport:
    account_index = 42
    api_key_index = 3

    def __init__(self) -> None:
        self.next_nonce_calls = 0
        self.create_calls = 0
        self.error: Exception | None = TimeoutError("sequencer response lost")

    def next_nonce(self) -> int:
        self.next_nonce_calls += 1
        return 77

    def create_order(self, **_kwargs):
        from app.execution.venues.lighter_actions import LighterActionAck

        self.create_calls += 1
        if self.error is not None:
            raise self.error
        return LighterActionAck(code=200, tx_hash="0xlate")

    def cancel_order(self, **_kwargs):  # pragma: no cover - not used here
        raise AssertionError("unexpected cancel")


def test_timeout_requires_read_only_reconciliation_before_any_resubmit(session) -> None:
    from app.execution.venues.lighter_actions import (
        LighterActionNeedsReconciliation,
        LighterOrderActions,
    )

    sessions = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    transport = AmbiguousTransport()
    actions = LighterOrderActions(session_factory=sessions, transport=transport)
    kwargs = dict(
        market=_market(),
        client_order_id="e-ambiguous-timeout",
        quantity=Decimal("0.02"),
        price=Decimal("3900"),
        is_ask=False,
        post_only=False,
    )

    with pytest.raises(TimeoutError, match="response lost"):
        actions.create_limit(**kwargs)

    transport.error = None
    with pytest.raises(LighterActionNeedsReconciliation, match="reconciliation"):
        actions.create_limit(**kwargs)

    assert transport.next_nonce_calls == 1
    assert transport.create_calls == 1


def test_provider_rejection_with_reserved_nonce_also_requires_reconciliation(session) -> None:
    from app.execution.venues.lighter_actions import (
        LighterActionAck,
        LighterActionNeedsReconciliation,
        LighterActionRejected,
        LighterOrderActions,
    )

    sessions = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    transport = AmbiguousTransport()
    transport.error = None
    transport.create_order = lambda **_kwargs: LighterActionAck(
        code=409,
        tx_hash="0xmaybe",
        message="invalid nonce",
    )
    actions = LighterOrderActions(session_factory=sessions, transport=transport)
    kwargs = dict(
        market=_market(),
        client_order_id="e-ambiguous-reject",
        quantity=Decimal("0.02"),
        price=Decimal("3900"),
        is_ask=False,
        post_only=False,
    )

    with pytest.raises(LighterActionRejected, match="409"):
        actions.create_limit(**kwargs)

    with pytest.raises(LighterActionNeedsReconciliation, match="reconciliation"):
        actions.create_limit(**kwargs)

    assert transport.next_nonce_calls == 1
