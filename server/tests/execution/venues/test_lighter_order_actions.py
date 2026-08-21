from __future__ import annotations

import ast
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.execution.venues.lighter_facts import LighterMarketFact


def _market(*, status: str = "active") -> LighterMarketFact:
    from datetime import UTC, datetime

    return LighterMarketFact(
        market_id=0,
        symbol="ETH",
        status=status,
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


def _sessions(session):
    return sessionmaker(bind=session.get_bind(), expire_on_commit=False)


class FakeLighterActionTransport:
    account_index = 42
    api_key_index = 3

    def __init__(self, *, nonces: list[int], ack=None) -> None:
        self.nonces = list(nonces)
        self.ack = ack
        self.next_nonce_calls = 0
        self.create_calls: list[dict[str, object]] = []
        self.cancel_calls: list[dict[str, object]] = []
        self.create_error: Exception | None = None
        self.cancel_error: Exception | None = None
        self.before_create = None

    def next_nonce(self) -> int:
        self.next_nonce_calls += 1
        if not self.nonces:
            raise AssertionError("unexpected next_nonce call")
        return self.nonces.pop(0)

    def create_order(self, **kwargs):
        self.create_calls.append(dict(kwargs))
        if self.before_create is not None:
            self.before_create()
        if self.create_error is not None:
            raise self.create_error
        return self.ack

    def cancel_order(self, **kwargs):
        self.cancel_calls.append(dict(kwargs))
        if self.cancel_error is not None:
            raise self.cancel_error
        return self.ack


def _accepted_ack():
    from app.execution.venues.lighter_actions import LighterActionAck

    return LighterActionAck(code=200, tx_hash="0xtx", message=None)


def test_limit_create_scales_exactly_and_commits_nonce_before_provider_io(session) -> None:
    from app.execution.venues.lighter_actions import LighterOrderActions
    from app.models.lighter_execution import LighterNonceReservation, LighterOrderIdentity

    sessions = _sessions(session)
    transport = FakeLighterActionTransport(nonces=[120], ack=_accepted_ack())

    def assert_durable_before_io() -> None:
        with sessions() as verifier:
            reservation = verifier.scalar(
                select(LighterNonceReservation).where(
                    LighterNonceReservation.replay_key == "CREATE:e-entry"
                )
            )
            assert reservation is not None
            assert reservation.nonce == 120
            assert reservation.state == "RESERVED"

    transport.before_create = assert_durable_before_io
    actions = LighterOrderActions(session_factory=sessions, transport=transport)

    ack = actions.create_limit(
        market=_market(),
        client_order_id="e-entry",
        quantity=Decimal("0.1250"),
        price=Decimal("4050.25"),
        is_ask=False,
        post_only=False,
    )

    assert ack == _accepted_ack()
    assert transport.next_nonce_calls == 1
    assert transport.create_calls == [
        {
            "market_index": 0,
            "client_order_index": pytest.ANY,
            "base_amount": 1250,
            "price": 405025,
            "is_ask": False,
            "order_type": 0,
            "time_in_force": 1,
            "reduce_only": False,
            "trigger_price": 0,
            "order_expiry": -1,
            "skip_nonce": 1,
            "nonce": 120,
            "api_key_index": 3,
        }
    ]
    client_index = int(transport.create_calls[0]["client_order_index"])
    assert client_index > 0

    with sessions() as verifier:
        identity = verifier.scalar(
            select(LighterOrderIdentity).where(
                LighterOrderIdentity.client_order_id == "e-entry"
            )
        )
        reservation = verifier.scalar(
            select(LighterNonceReservation).where(
                LighterNonceReservation.replay_key == "CREATE:e-entry"
            )
        )
        assert identity is not None
        assert identity.client_order_index == client_index
        assert reservation is not None
        assert reservation.state == "CONSUMED"
        assert reservation.consumed_at is not None


def test_post_only_limit_uses_provider_post_only_tif(session) -> None:
    from app.execution.venues.lighter_actions import LighterOrderActions

    transport = FakeLighterActionTransport(nonces=[10], ack=_accepted_ack())
    actions = LighterOrderActions(session_factory=_sessions(session), transport=transport)

    actions.create_limit(
        market=_market(),
        client_order_id="e-post",
        quantity=Decimal("0.01"),
        price=Decimal("4000.00"),
        is_ask=True,
        post_only=True,
    )

    assert transport.create_calls[0]["time_in_force"] == 2
    assert transport.create_calls[0]["order_type"] == 0
    assert transport.create_calls[0]["reduce_only"] is False


def test_successful_action_is_never_blindly_resubmitted(session) -> None:
    from app.execution.venues.lighter_actions import (
        LighterActionAlreadyConsumed,
        LighterOrderActions,
    )

    transport = FakeLighterActionTransport(nonces=[10], ack=_accepted_ack())
    actions = LighterOrderActions(session_factory=_sessions(session), transport=transport)
    kwargs = dict(
        market=_market(),
        client_order_id="e-once",
        quantity=Decimal("0.01"),
        price=Decimal("4000"),
        is_ask=False,
        post_only=False,
    )

    actions.create_limit(**kwargs)
    with pytest.raises(LighterActionAlreadyConsumed, match="CREATE:e-once"):
        actions.create_limit(**kwargs)

    assert transport.next_nonce_calls == 1
    assert len(transport.create_calls) == 1


def test_timeout_keeps_reserved_nonce_and_exact_retry_reuses_it(session) -> None:
    from app.execution.venues.lighter_actions import LighterOrderActions
    from app.models.lighter_execution import LighterNonceReservation

    sessions = _sessions(session)
    transport = FakeLighterActionTransport(nonces=[77], ack=_accepted_ack())
    actions = LighterOrderActions(session_factory=sessions, transport=transport)
    kwargs = dict(
        market=_market(),
        client_order_id="e-timeout",
        quantity=Decimal("0.02"),
        price=Decimal("3900"),
        is_ask=False,
        post_only=False,
    )

    transport.create_error = TimeoutError("sequencer response lost")
    with pytest.raises(TimeoutError, match="response lost"):
        actions.create_limit(**kwargs)

    with sessions() as verifier:
        reservation = verifier.scalar(
            select(LighterNonceReservation).where(
                LighterNonceReservation.replay_key == "CREATE:e-timeout"
            )
        )
        assert reservation is not None
        assert reservation.state == "RESERVED"
        assert reservation.nonce == 77

    transport.create_error = None
    actions.create_limit(**kwargs)

    assert transport.next_nonce_calls == 1
    assert [call["nonce"] for call in transport.create_calls] == [77, 77]


def test_changed_payload_cannot_reuse_same_action_identity_after_timeout(session) -> None:
    from app.execution.venues.lighter_actions import (
        LighterActionReplayMismatch,
        LighterOrderActions,
    )

    transport = FakeLighterActionTransport(nonces=[77], ack=_accepted_ack())
    actions = LighterOrderActions(session_factory=_sessions(session), transport=transport)
    transport.create_error = TimeoutError("unknown result")

    with pytest.raises(TimeoutError):
        actions.create_limit(
            market=_market(),
            client_order_id="e-bound",
            quantity=Decimal("0.02"),
            price=Decimal("3900"),
            is_ask=False,
            post_only=False,
        )

    transport.create_error = None
    with pytest.raises(LighterActionReplayMismatch, match="request"):
        actions.create_limit(
            market=_market(),
            client_order_id="e-bound",
            quantity=Decimal("0.03"),
            price=Decimal("3900"),
            is_ask=False,
            post_only=False,
        )

    assert transport.next_nonce_calls == 1
    assert len(transport.create_calls) == 1


def test_cancel_uses_existing_numeric_order_identity_and_new_nonce(session) -> None:
    from app.execution.venues.lighter_actions import LighterOrderActions

    transport = FakeLighterActionTransport(nonces=[120, 121], ack=_accepted_ack())
    actions = LighterOrderActions(session_factory=_sessions(session), transport=transport)

    actions.create_limit(
        market=_market(),
        client_order_id="e-cancel",
        quantity=Decimal("0.01"),
        price=Decimal("4000"),
        is_ask=False,
        post_only=False,
    )
    created_index = int(transport.create_calls[0]["client_order_index"])

    actions.cancel(market=_market(), client_order_id="e-cancel")

    assert transport.cancel_calls == [
        {
            "market_index": 0,
            "order_index": created_index,
            "skip_nonce": 1,
            "nonce": 121,
            "api_key_index": 3,
        }
    ]


def test_reduce_market_is_always_ioc_and_reduce_only(session) -> None:
    from app.execution.venues.lighter_actions import LighterOrderActions

    transport = FakeLighterActionTransport(nonces=[200], ack=_accepted_ack())
    actions = LighterOrderActions(session_factory=_sessions(session), transport=transport)

    actions.reduce_market(
        market=_market(),
        client_order_id="x-flatten",
        quantity=Decimal("0.0500"),
        worst_price=Decimal("3850.75"),
        is_ask=True,
    )

    call = transport.create_calls[0]
    assert call["market_index"] == 0
    assert int(call["client_order_index"]) > 0
    assert call["base_amount"] == 500
    assert call["price"] == 385075
    assert call["is_ask"] is True
    assert call["order_type"] == 1
    assert call["time_in_force"] == 0
    assert call["order_expiry"] == 0
    assert call["reduce_only"] is True
    assert call["trigger_price"] == 0
    assert call["skip_nonce"] == 1
    assert call["nonce"] == 200
    assert call["api_key_index"] == 3


def test_provider_rejection_keeps_nonce_unresolved_for_later_reconciliation(session) -> None:
    from app.execution.venues.lighter_actions import (
        LighterActionAck,
        LighterActionRejected,
        LighterOrderActions,
    )
    from app.models.lighter_execution import LighterNonceReservation

    sessions = _sessions(session)
    transport = FakeLighterActionTransport(
        nonces=[300],
        ack=LighterActionAck(code=409, tx_hash="0xmaybe", message="invalid nonce"),
    )
    actions = LighterOrderActions(session_factory=sessions, transport=transport)

    with pytest.raises(LighterActionRejected, match="409"):
        actions.create_limit(
            market=_market(),
            client_order_id="e-rejected",
            quantity=Decimal("0.01"),
            price=Decimal("4000"),
            is_ask=False,
            post_only=False,
        )

    with sessions() as verifier:
        reservation = verifier.scalar(
            select(LighterNonceReservation).where(
                LighterNonceReservation.replay_key == "CREATE:e-rejected"
            )
        )
        assert reservation is not None
        assert reservation.state == "RESERVED"


def test_invalid_market_or_unrepresentable_order_fails_before_nonce_or_transport(session) -> None:
    from app.execution.venues.lighter_actions import LighterOrderActionError, LighterOrderActions

    transport = FakeLighterActionTransport(nonces=[1], ack=_accepted_ack())
    actions = LighterOrderActions(session_factory=_sessions(session), transport=transport)

    cases = (
        dict(market=_market(status="inactive"), quantity=Decimal("0.01"), price=Decimal("4000")),
        dict(market=_market(), quantity=Decimal("0.00001"), price=Decimal("4000")),
        dict(market=_market(), quantity=Decimal("0.01"), price=Decimal("4000.001")),
        dict(market=_market(), quantity=Decimal("0.001"), price=Decimal("100")),
    )
    for index, case in enumerate(cases):
        with pytest.raises(LighterOrderActionError):
            actions.create_limit(
                client_order_id=f"e-invalid-{index}",
                is_ask=False,
                post_only=False,
                **case,
            )

    assert transport.next_nonce_calls == 0
    assert transport.create_calls == []


def test_action_request_binding_is_append_only_and_module_has_no_sdk_or_live_factory(session) -> None:
    from app.execution.venues import lighter_actions
    from app.execution.venues.lighter_actions import LighterOrderActions
    from app.models.lighter_execution import LighterOrderActionBinding

    sessions = _sessions(session)
    transport = FakeLighterActionTransport(nonces=[9], ack=_accepted_ack())
    actions = LighterOrderActions(session_factory=sessions, transport=transport)
    actions.create_limit(
        market=_market(),
        client_order_id="e-binding",
        quantity=Decimal("0.01"),
        price=Decimal("4000"),
        is_ask=False,
        post_only=False,
    )

    with sessions() as verifier:
        binding = verifier.scalar(
            select(LighterOrderActionBinding).where(
                LighterOrderActionBinding.action_key == "CREATE:e-binding"
            )
        )
        assert binding is not None
        with pytest.raises(DBAPIError):
            verifier.execute(
                text(
                    "UPDATE lighter_order_action_bindings "
                    "SET request_hash = :value WHERE id = :id"
                ),
                {"value": "0" * 64, "id": binding.id},
            )
            verifier.commit()
        verifier.rollback()

    tree = ast.parse(open(lighter_actions.__file__, encoding="utf-8").read())
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert "lighter" not in imported_roots
    for forbidden in ("activate_live", "live_enabled", "SIGNALAI_LIVE"):
        assert forbidden not in open(lighter_actions.__file__, encoding="utf-8").read()
