from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from app.execution.venues.lighter_actions import LighterActionAck
from app.execution.venues.lighter_auth import LighterServerCredentials


_PRIVATE_KEY = "ab" * 32


def _credentials(*, environment: str = "testnet", purpose: str = "trade"):
    return LighterServerCredentials(
        account_index=42,
        api_key_index=3,
        api_private_key=_PRIVATE_KEY,
        environment=environment,
        purpose=purpose,
    )


class _FakeTxApi:
    def __init__(self, *, nonce: int = 100, error: Exception | None = None) -> None:
        self.nonce = nonce
        self.error = error
        self.calls: list[tuple[int, int]] = []

    async def next_nonce(self, account_index: int, api_key_index: int):
        self.calls.append((account_index, api_key_index))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(code=200, message=None, nonce=self.nonce)


class _FakeSignerClient:
    def __init__(self) -> None:
        self.tx_api = _FakeTxApi()
        self.check_result: str | None = None
        self.check_error: Exception | None = None
        self.create_result = (
            SimpleNamespace(),
            SimpleNamespace(code=200, tx_hash="0xcreate", message=None),
            None,
        )
        self.cancel_result = (
            SimpleNamespace(),
            SimpleNamespace(code=200, tx_hash="0xcancel", message=None),
            None,
        )
        self.create_error: Exception | None = None
        self.cancel_error: Exception | None = None
        self.create_calls: list[dict[str, object]] = []
        self.cancel_calls: list[dict[str, object]] = []

    def check_client(self):
        if self.check_error is not None:
            raise self.check_error
        return self.check_result

    async def create_order(self, **kwargs):
        self.create_calls.append(dict(kwargs))
        if self.create_error is not None:
            raise self.create_error
        return self.create_result

    async def cancel_order(self, **kwargs):
        self.cancel_calls.append(dict(kwargs))
        if self.cancel_error is not None:
            raise self.cancel_error
        return self.cancel_result


class _Factory:
    def __init__(self, client: _FakeSignerClient | None = None) -> None:
        self.client = client or _FakeSignerClient()
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.client


def test_factory_is_pinned_to_testnet_and_single_credential_scope() -> None:
    from app.execution.venues.lighter_sdk_transport import (
        build_lighter_testnet_transport,
    )

    factory = _Factory()
    transport = build_lighter_testnet_transport(
        _credentials(), signer_client_factory=factory
    )

    assert factory.calls == [
        {
            "url": "https://testnet.zklighter.elliot.ai",
            "account_index": 42,
            "api_private_keys": {3: _PRIVATE_KEY},
            "chain_id": 300,
        }
    ]
    assert transport.base_url == "https://testnet.zklighter.elliot.ai"
    assert transport.chain_id == 300
    assert transport.account_index == 42
    assert transport.api_key_index == 3
    assert transport.eligible_for_live is False
    assert _PRIVATE_KEY not in repr(transport)


def test_factory_refuses_non_testnet_trade_credentials_before_sdk_construction() -> None:
    from app.execution.venues.lighter_sdk_transport import (
        LighterSdkTransportError,
        build_lighter_testnet_transport,
    )

    for credentials in (
        _credentials(environment="live"),
        _credentials(purpose="read"),
    ):
        factory = _Factory()
        with pytest.raises(LighterSdkTransportError, match="testnet trade"):
            build_lighter_testnet_transport(
                credentials, signer_client_factory=factory
            )
        assert factory.calls == []


def test_check_client_and_next_nonce_use_exact_bound_scope() -> None:
    from app.execution.venues.lighter_sdk_transport import (
        build_lighter_testnet_transport,
    )

    client = _FakeSignerClient()
    client.tx_api = _FakeTxApi(nonce=777)
    transport = build_lighter_testnet_transport(
        _credentials(), signer_client_factory=_Factory(client)
    )

    assert transport.check_client() is None
    assert transport.next_nonce() == 777
    assert client.tx_api.calls == [(42, 3)]


def test_create_and_cancel_forward_explicit_nonce_calls_and_normalize_ack() -> None:
    from app.execution.venues.lighter_sdk_transport import (
        build_lighter_testnet_transport,
    )

    client = _FakeSignerClient()
    transport = build_lighter_testnet_transport(
        _credentials(), signer_client_factory=_Factory(client)
    )
    create_kwargs = {
        "market_index": 0,
        "client_order_index": 123,
        "base_amount": 100,
        "price": 400000,
        "is_ask": False,
        "order_type": 0,
        "time_in_force": 2,
        "reduce_only": False,
        "trigger_price": 0,
        "order_expiry": -1,
        "skip_nonce": 1,
        "nonce": 500,
        "api_key_index": 3,
    }
    cancel_kwargs = {
        "market_index": 0,
        "order_index": 123,
        "skip_nonce": 1,
        "nonce": 501,
        "api_key_index": 3,
    }

    create_ack = transport.create_order(**create_kwargs)
    cancel_ack = transport.cancel_order(**cancel_kwargs)

    assert create_ack == LighterActionAck(code=200, tx_hash="0xcreate", message=None)
    assert cancel_ack == LighterActionAck(code=200, tx_hash="0xcancel", message=None)
    assert client.create_calls == [create_kwargs]
    assert client.cancel_calls == [cancel_kwargs]


def test_transport_refuses_automatic_or_foreign_nonce_scope_before_sdk_io() -> None:
    from app.execution.venues.lighter_sdk_transport import (
        LighterSdkTransportError,
        build_lighter_testnet_transport,
    )

    client = _FakeSignerClient()
    transport = build_lighter_testnet_transport(
        _credentials(), signer_client_factory=_Factory(client)
    )

    with pytest.raises(LighterSdkTransportError, match="explicit nonce"):
        transport.cancel_order(
            market_index=0,
            order_index=123,
            skip_nonce=0,
            nonce=500,
            api_key_index=3,
        )
    with pytest.raises(LighterSdkTransportError, match="credential scope"):
        transport.cancel_order(
            market_index=0,
            order_index=123,
            skip_nonce=1,
            nonce=500,
            api_key_index=4,
        )

    assert client.cancel_calls == []


def test_provider_failures_are_sanitized_without_secret_or_provider_text() -> None:
    from app.execution.venues.lighter_sdk_transport import (
        LighterSdkTransportError,
        build_lighter_testnet_transport,
    )

    client = _FakeSignerClient()
    provider_text = f"bad key {_PRIVATE_KEY}"
    client.check_error = RuntimeError(provider_text)
    transport = build_lighter_testnet_transport(
        _credentials(), signer_client_factory=_Factory(client)
    )

    with pytest.raises(LighterSdkTransportError) as captured:
        transport.check_client()
    rendered = str(captured.value)
    assert _PRIVATE_KEY not in rendered
    assert provider_text not in rendered

    client.check_error = None
    client.create_result = (None, None, provider_text)
    with pytest.raises(LighterSdkTransportError) as captured:
        transport.create_order(
            market_index=0,
            client_order_index=123,
            base_amount=100,
            price=400000,
            is_ask=False,
            order_type=0,
            time_in_force=2,
            reduce_only=False,
            trigger_price=0,
            order_expiry=-1,
            skip_nonce=1,
            nonce=500,
            api_key_index=3,
        )
    rendered = str(captured.value)
    assert _PRIVATE_KEY not in rendered
    assert provider_text not in rendered


def test_sync_transport_can_bridge_sdk_coroutine_inside_running_event_loop() -> None:
    from app.execution.venues.lighter_sdk_transport import (
        build_lighter_testnet_transport,
    )

    client = _FakeSignerClient()
    client.tx_api = _FakeTxApi(nonce=909)
    transport = build_lighter_testnet_transport(
        _credentials(), signer_client_factory=_Factory(client)
    )

    async def invoke_from_async_context() -> int:
        return transport.next_nonce()

    assert asyncio.run(invoke_from_async_context()) == 909


def test_sdk_client_and_async_calls_share_one_persistent_loop_thread() -> None:
    from app.execution.venues.lighter_sdk_transport import (
        build_lighter_testnet_transport,
    )

    observed: dict[str, object] = {}

    class LoopAwareTxApi:
        async def next_nonce(self, account_index: int, api_key_index: int):
            observed.setdefault("call_loops", []).append(id(asyncio.get_running_loop()))
            observed.setdefault("call_threads", []).append(threading.get_ident())
            return SimpleNamespace(code=200, message=None, nonce=321)

    class LoopAwareClient(_FakeSignerClient):
        def __init__(self) -> None:
            super().__init__()
            self.tx_api = LoopAwareTxApi()

    client = LoopAwareClient()

    def factory(**kwargs):
        observed["factory_loop"] = id(asyncio.get_running_loop())
        observed["factory_thread"] = threading.get_ident()
        return client

    transport = build_lighter_testnet_transport(
        _credentials(), signer_client_factory=factory
    )
    assert transport.next_nonce() == 321
    assert transport.next_nonce() == 321

    assert observed["call_loops"] == [observed["factory_loop"], observed["factory_loop"]]
    assert observed["call_threads"] == [
        observed["factory_thread"],
        observed["factory_thread"],
    ]
