from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

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
        observed_at=datetime(2026, 8, 21, 13, 0, tzinfo=UTC),
    )


class _Transport:
    account_index = 42
    api_key_index = 3

    def next_nonce(self) -> int:
        return 700

    def create_order(self, **kwargs):
        from app.execution.venues.lighter_actions import LighterActionAck

        return LighterActionAck(code=200, tx_hash="0xlock-order")

    def cancel_order(self, **kwargs):
        raise AssertionError("cancel not expected")


def test_action_layer_acquires_identity_before_replay_and_nonce_scope_locks(
    session, monkeypatch
) -> None:
    """Keep one global advisory-lock order with the standalone SAI-069 seam."""
    from app.execution.venues import lighter_actions

    events: list[str] = []
    original_resolver = lighter_actions.resolve_lighter_order_identity

    def recording_resolver(*args, **kwargs):
        events.append("identity")
        return original_resolver(*args, **kwargs)

    def recording_action_lock(db, namespace: str, identity: str) -> None:
        if namespace == "lighter-replay-key":
            events.append("replay")
        elif namespace == "lighter-nonce-scope":
            events.append("scope")

    monkeypatch.setattr(lighter_actions, "resolve_lighter_order_identity", recording_resolver)
    monkeypatch.setattr(lighter_actions, "_lock", recording_action_lock)

    sessions = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    actions = lighter_actions.LighterOrderActions(
        session_factory=sessions,
        transport=_Transport(),
    )
    actions.create_limit(
        market=_market(),
        client_order_id="e-lock-order",
        quantity=Decimal("0.01"),
        price=Decimal("4000"),
        is_ask=False,
        post_only=False,
    )

    assert events[:3] == ["identity", "replay", "scope"]
