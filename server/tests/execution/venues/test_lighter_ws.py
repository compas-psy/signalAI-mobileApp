from __future__ import annotations

from decimal import Decimal

import pytest


def _order_payload(*, account_index: int = 42) -> dict:
    return {
        "order_index": 9001,
        "client_order_index": 123456,
        "order_id": "9001",
        "client_order_id": "123456",
        "market_index": 0,
        "owner_account_index": account_index,
        "initial_base_amount": "0.0100",
        "price": "4000.25",
        "nonce": 77,
        "remaining_base_amount": "0.0040",
        "is_ask": False,
        "base_size": 100,
        "base_price": 400025,
        "filled_base_amount": "0.0060",
        "filled_quote_amount": "24.0015",
        "side": "buy",
        "type": "limit",
        "time_in_force": "good-till-time",
        "reduce_only": False,
        "trigger_price": "0",
        "order_expiry": -1,
        "status": "open",
        "trigger_status": "na",
        "trigger_time": 0,
        "parent_order_index": 0,
        "parent_order_id": "0",
        "to_trigger_order_id_0": "0",
        "to_trigger_order_id_1": "0",
        "to_cancel_order_id_0": "0",
        "integrator_fee_collector_index": "0",
        "integrator_taker_fee": "0",
        "integrator_maker_fee": "0",
        "block_height": 198321831,
        "timestamp": 1773854156654,
        "created_at": 1773854156000,
        "updated_at": 1773854156654,
        "transaction_time": 1773854156686065,
    }


def _trade_payload(
    *,
    ask_account_id: int = 10,
    bid_account_id: int = 42,
    is_maker_ask: bool = True,
) -> dict:
    return {
        "trade_id": 16164557907,
        "trade_id_str": "16164557907",
        "tx_hash": "0xabc",
        "type": "trade",
        "market_id": 0,
        "size": "0.0060",
        "price": "4000.25",
        "usd_amount": "24.0015",
        "ask_id": 9002,
        "ask_id_str": "9002",
        "bid_id": 9001,
        "bid_id_str": "9001",
        "ask_client_id": 222222,
        "ask_client_id_str": "222222",
        "bid_client_id": 123456,
        "bid_client_id_str": "123456",
        "ask_account_id": ask_account_id,
        "bid_account_id": bid_account_id,
        "is_maker_ask": is_maker_ask,
        "block_height": 198321831,
        "timestamp": 1773854156654,
        "taker_fee": 196,
        "maker_fee": 28,
        "transaction_time": 1773854156686065,
    }


def test_builds_documented_order_and_trade_subscriptions() -> None:
    from app.execution.venues.lighter_ws import build_lighter_account_subscriptions

    subscriptions = build_lighter_account_subscriptions(
        account_index=42,
        auth_token="signed-auth-token",
    )

    assert subscriptions == (
        {
            "type": "subscribe",
            "channel": "account_all_orders/42",
            "auth": "signed-auth-token",
        },
        {
            "type": "subscribe",
            "channel": "account_all_trades/42",
        },
    )

    with pytest.raises(ValueError, match="auth_token"):
        build_lighter_account_subscriptions(account_index=42, auth_token="")


def test_order_update_normalizes_account_bound_order_event() -> None:
    from app.execution.venues.lighter_ws import decode_lighter_account_message

    batch = decode_lighter_account_message(
        {
            "type": "update/account_all_orders",
            "channel": "account_all_orders:42",
            "orders": {"0": [_order_payload()]},
        },
        account_index=42,
    )

    assert batch.control is None
    assert len(batch.orders) == 1
    assert batch.fills == ()
    event = batch.orders[0]
    assert event.account_index == 42
    assert event.market_index == 0
    assert event.provider_order_id == "9001"
    assert event.client_order_index == 123456
    assert event.status == "open"
    assert event.initial_quantity == Decimal("0.0100")
    assert event.remaining_quantity == Decimal("0.0040")
    assert event.filled_quantity == Decimal("0.0060")
    assert event.price == Decimal("4000.25")
    assert event.is_ask is False
    assert event.reduce_only is False
    assert event.event_at.tzinfo is not None
    assert event.event_key

    replay = decode_lighter_account_message(
        {
            "type": "update/account_all_orders",
            "channel": "account_all_orders:42",
            "orders": {"0": [_order_payload()]},
        },
        account_index=42,
    )
    assert replay.orders[0].event_key == event.event_key


def test_trade_update_normalizes_account_side_fill_and_fee_role() -> None:
    from app.execution.venues.lighter_ws import decode_lighter_account_message

    batch = decode_lighter_account_message(
        {
            "type": "update/account_all_trades",
            "channel": "account_all_trades:42",
            "trades": {"0": [_trade_payload()]},
        },
        account_index=42,
    )

    assert batch.orders == ()
    assert len(batch.fills) == 1
    fill = batch.fills[0]
    assert fill.provider_trade_id == "16164557907"
    assert fill.market_index == 0
    assert fill.client_order_index == 123456
    assert fill.side == "BUY"
    assert fill.quantity == Decimal("0.0060")
    assert fill.price == Decimal("4000.25")
    assert fill.is_maker is False
    assert fill.fee_raw == 196
    assert fill.event_at.tzinfo is not None
    assert fill.event_key


def test_trade_update_maps_ask_side_maker_fill() -> None:
    from app.execution.venues.lighter_ws import decode_lighter_account_message

    batch = decode_lighter_account_message(
        {
            "type": "update/account_all_trades",
            "channel": "account_all_trades:42",
            "trades": {
                "0": [
                    _trade_payload(
                        ask_account_id=42,
                        bid_account_id=10,
                        is_maker_ask=True,
                    )
                ]
            },
        },
        account_index=42,
    )

    fill = batch.fills[0]
    assert fill.client_order_index == 222222
    assert fill.side == "SELL"
    assert fill.is_maker is True
    assert fill.fee_raw == 28


def test_self_trade_emits_two_distinct_account_fill_events() -> None:
    from app.execution.venues.lighter_ws import decode_lighter_account_message

    batch = decode_lighter_account_message(
        {
            "type": "update/account_all_trades",
            "channel": "account_all_trades:42",
            "trades": {
                "0": [
                    _trade_payload(
                        ask_account_id=42,
                        bid_account_id=42,
                        is_maker_ask=False,
                    )
                ]
            },
        },
        account_index=42,
    )

    assert len(batch.fills) == 2
    assert {fill.side for fill in batch.fills} == {"BUY", "SELL"}
    assert len({fill.event_key for fill in batch.fills}) == 2
    by_side = {fill.side: fill for fill in batch.fills}
    assert by_side["BUY"].is_maker is True
    assert by_side["BUY"].fee_raw == 28
    assert by_side["SELL"].is_maker is False
    assert by_side["SELL"].fee_raw == 196


def test_snapshot_empty_trades_and_ping_are_control_safe() -> None:
    from app.execution.venues.lighter_ws import decode_lighter_account_message

    snapshot = decode_lighter_account_message(
        {
            "type": "subscribed/account_all_trades",
            "channel": "account_all_trades:42",
            "trades": [],
            "total_volume": 0.0,
            "monthly_volume": 0.0,
            "weekly_volume": 0.0,
            "daily_volume": 0.0,
        },
        account_index=42,
    )
    assert snapshot.orders == ()
    assert snapshot.fills == ()
    assert snapshot.control is None

    ping = decode_lighter_account_message({"type": "ping"}, account_index=42)
    assert ping.control == "PONG"
    assert ping.orders == ()
    assert ping.fills == ()


def test_account_channel_and_numeric_payload_fail_closed() -> None:
    from app.execution.venues.lighter_ws import (
        LighterWsProtocolError,
        decode_lighter_account_message,
    )

    with pytest.raises(LighterWsProtocolError, match="account"):
        decode_lighter_account_message(
            {
                "type": "update/account_all_orders",
                "channel": "account_all_orders:43",
                "orders": {"0": [_order_payload()]},
            },
            account_index=42,
        )

    broken = _trade_payload()
    broken["size"] = "not-a-number"
    with pytest.raises(LighterWsProtocolError, match="size"):
        decode_lighter_account_message(
            {
                "type": "update/account_all_trades",
                "channel": "account_all_trades:42",
                "trades": {"0": [broken]},
            },
            account_index=42,
        )


def test_sai071_protocol_has_no_execution_or_transaction_methods() -> None:
    from app.execution.venues import lighter_ws

    for forbidden in (
        "create_order",
        "cancel_order",
        "reduce_market",
        "send_tx",
        "arm_protection",
        "reconcile_submission",
        "reconcile_protection",
    ):
        assert not hasattr(lighter_ws, forbidden)
