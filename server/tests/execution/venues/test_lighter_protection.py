from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import ANY

import pytest
from sqlalchemy import select
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
        observed_at=datetime(2026, 8, 21, 15, 0, tzinfo=UTC),
    )


def _sessions(session):
    return sessionmaker(bind=session.get_bind(), expire_on_commit=False)


class _Transport:
    account_index = 42
    api_key_index = 3

    def __init__(self, *, nonce: int = 200) -> None:
        from app.execution.venues.lighter_actions import LighterActionAck

        self.nonce = nonce
        self.next_nonce_calls = 0
        self.create_calls: list[dict[str, object]] = []
        self.create_error: Exception | None = None
        self.ack = LighterActionAck(code=200, tx_hash="0xprotect")

    def next_nonce(self) -> int:
        self.next_nonce_calls += 1
        return self.nonce

    def create_order(self, **kwargs):
        self.create_calls.append(dict(kwargs))
        if self.create_error is not None:
            raise self.create_error
        return self.ack

    def cancel_order(self, **kwargs):
        raise AssertionError("protection must not cancel an order")


def test_long_position_stop_is_position_tied_reduce_only_and_scaled(session) -> None:
    from app.execution.venues.lighter_actions import LighterOrderActions
    from app.models.lighter_execution import LighterNonceReservation, LighterOrderActionBinding

    transport = _Transport(nonce=200)
    sessions = _sessions(session)
    actions = LighterOrderActions(session_factory=sessions, transport=transport)

    ack = actions.arm_position_stop(
        market=_market(),
        client_order_id="p-long-1",
        position_side="LONG",
        trigger_price=Decimal("3800.00"),
        worst_price=Decimal("3780.00"),
    )

    assert ack.tx_hash == "0xprotect"
    assert transport.create_calls == [
        {
            "market_index": 0,
            "client_order_index": ANY,
            "base_amount": 0,
            "price": 378000,
            "is_ask": True,
            "order_type": 3,
            "time_in_force": 1,
            "reduce_only": True,
            "trigger_price": 380000,
            "order_expiry": -1,
            "skip_nonce": 1,
            "nonce": 200,
            "api_key_index": 3,
        }
    ]

    with sessions() as db:
        binding = db.scalar(
            select(LighterOrderActionBinding).where(
                LighterOrderActionBinding.action_key == "PROTECT:p-long-1"
            )
        )
        reservation = db.scalar(
            select(LighterNonceReservation).where(
                LighterNonceReservation.replay_key == "PROTECT:p-long-1"
            )
        )
        assert binding is not None
        assert binding.action_type == "PROTECT"
        assert reservation is not None
        assert reservation.state == "CONSUMED"


def test_short_position_stop_buys_back_with_adverse_slippage_cap(session) -> None:
    from app.execution.venues.lighter_actions import LighterOrderActions

    transport = _Transport(nonce=201)
    actions = LighterOrderActions(session_factory=_sessions(session), transport=transport)

    actions.arm_position_stop(
        market=_market(),
        client_order_id="p-short-1",
        position_side="SHORT",
        trigger_price=Decimal("4200"),
        worst_price=Decimal("4225"),
    )

    call = transport.create_calls[0]
    assert call["base_amount"] == 0
    assert call["is_ask"] is False
    assert call["reduce_only"] is True
    assert call["order_type"] == 3
    assert call["trigger_price"] == 420000
    assert call["price"] == 422500


def test_stop_limit_must_allow_slippage_only_in_exit_direction(session) -> None:
    from app.execution.venues.lighter_actions import LighterOrderActionError, LighterOrderActions

    transport = _Transport()
    actions = LighterOrderActions(session_factory=_sessions(session), transport=transport)

    with pytest.raises(LighterOrderActionError, match="LONG"):
        actions.arm_position_stop(
            market=_market(),
            client_order_id="p-bad-long",
            position_side="LONG",
            trigger_price=Decimal("3800"),
            worst_price=Decimal("3810"),
        )

    with pytest.raises(LighterOrderActionError, match="SHORT"):
        actions.arm_position_stop(
            market=_market(),
            client_order_id="p-bad-short",
            position_side="SHORT",
            trigger_price=Decimal("4200"),
            worst_price=Decimal("4190"),
        )

    assert transport.next_nonce_calls == 0
    assert transport.create_calls == []


def test_stop_prices_must_match_provider_precision_before_nonce_io(session) -> None:
    from app.execution.venues.lighter_actions import LighterOrderActionError, LighterOrderActions

    transport = _Transport()
    actions = LighterOrderActions(session_factory=_sessions(session), transport=transport)

    with pytest.raises(LighterOrderActionError, match="trigger_price"):
        actions.arm_position_stop(
            market=_market(),
            client_order_id="p-precision",
            position_side="LONG",
            trigger_price=Decimal("3800.001"),
            worst_price=Decimal("3780"),
        )

    assert transport.next_nonce_calls == 0


def test_timeout_keeps_protection_nonce_reserved_and_exact_retry_reuses_it(session) -> None:
    from app.execution.venues.lighter_actions import LighterOrderActions
    from app.models.lighter_execution import LighterNonceReservation

    sessions = _sessions(session)
    transport = _Transport(nonce=333)
    actions = LighterOrderActions(session_factory=sessions, transport=transport)
    kwargs = dict(
        market=_market(),
        client_order_id="p-timeout",
        position_side="LONG",
        trigger_price=Decimal("3800"),
        worst_price=Decimal("3780"),
    )

    transport.create_error = TimeoutError("sequencer response lost")
    with pytest.raises(TimeoutError, match="response lost"):
        actions.arm_position_stop(**kwargs)

    with sessions() as db:
        reservation = db.scalar(
            select(LighterNonceReservation).where(
                LighterNonceReservation.replay_key == "PROTECT:p-timeout"
            )
        )
        assert reservation is not None
        assert reservation.nonce == 333
        assert reservation.state == "RESERVED"

    transport.create_error = None
    actions.arm_position_stop(**kwargs)
    assert transport.next_nonce_calls == 1
    assert [call["nonce"] for call in transport.create_calls] == [333, 333]


def test_same_protection_identity_cannot_be_mutated_on_retry(session) -> None:
    from app.execution.venues.lighter_actions import (
        LighterActionReplayMismatch,
        LighterOrderActions,
    )

    transport = _Transport(nonce=444)
    transport.create_error = TimeoutError("lost")
    actions = LighterOrderActions(session_factory=_sessions(session), transport=transport)

    with pytest.raises(TimeoutError):
        actions.arm_position_stop(
            market=_market(),
            client_order_id="p-immutable",
            position_side="LONG",
            trigger_price=Decimal("3800"),
            worst_price=Decimal("3780"),
        )

    with pytest.raises(LighterActionReplayMismatch, match="different request"):
        actions.arm_position_stop(
            market=_market(),
            client_order_id="p-immutable",
            position_side="LONG",
            trigger_price=Decimal("3790"),
            worst_price=Decimal("3770"),
        )

    assert transport.next_nonce_calls == 1
    assert len(transport.create_calls) == 1
