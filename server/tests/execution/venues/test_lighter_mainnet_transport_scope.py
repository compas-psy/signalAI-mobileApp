from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.execution.venues.lighter_auth import LighterServerCredentials


_PRIVATE_KEY = "cd" * 32
_SNAPSHOT_HASH = "a" * 64
_GENERATION_ID = "gen-live-1"


def _credentials(
    *,
    environment: str = "live",
    purpose: str = "trade",
    generation_id: str | None = _GENERATION_ID,
    account_index: int = 42,
    api_key_index: int = 7,
) -> LighterServerCredentials:
    return LighterServerCredentials(
        account_index=account_index,
        api_key_index=api_key_index,
        api_private_key=_PRIVATE_KEY,
        environment=environment,
        purpose=purpose,
        credential_generation_id=generation_id,
    )


class _FakeTxApi:
    async def next_nonce(self, account_index: int, api_key_index: int):
        return SimpleNamespace(code=200, message=None, nonce=321)


class _FakeSignerClient:
    def __init__(self) -> None:
        self.tx_api = _FakeTxApi()

    def check_client(self):
        return None

    async def create_order(self, **kwargs):
        return (
            SimpleNamespace(),
            SimpleNamespace(code=200, tx_hash="0xmainnet-create", message=None),
            None,
        )

    async def cancel_order(self, **kwargs):
        return (
            SimpleNamespace(),
            SimpleNamespace(code=200, tx_hash="0xmainnet-cancel", message=None),
            None,
        )


class _Factory:
    def __init__(self) -> None:
        self.client = _FakeSignerClient()
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.client


def _scope(**overrides):
    from app.execution.venues.lighter_sdk_transport import LighterMainnetCanaryScope

    values = {
        "snapshot_hash": _SNAPSHOT_HASH,
        "credential_generation_id": _GENERATION_ID,
        "account_index": 42,
        "api_key_index": 7,
    }
    values.update(overrides)
    return LighterMainnetCanaryScope(**values)


def test_mainnet_factory_requires_exact_canary_snapshot_and_credential_scope() -> None:
    from app.execution.venues.lighter_sdk_transport import build_lighter_mainnet_transport

    factory = _Factory()
    transport = build_lighter_mainnet_transport(
        _credentials(), canary_scope=_scope(), signer_client_factory=factory
    )

    assert factory.calls == [
        {
            "url": "https://mainnet.zklighter.elliot.ai",
            "account_index": 42,
            "api_private_keys": {7: _PRIVATE_KEY},
        }
    ]
    assert transport.base_url == "https://mainnet.zklighter.elliot.ai"
    assert transport.account_index == 42
    assert transport.api_key_index == 7
    assert transport.credential_generation_id == _GENERATION_ID
    assert transport.canary_snapshot_hash == _SNAPSHOT_HASH
    assert transport.eligible_for_canary_transport is True
    assert transport.eligible_for_live is False
    rendered = repr(transport)
    assert _PRIVATE_KEY not in rendered
    assert _SNAPSHOT_HASH in rendered


def test_mainnet_factory_refuses_environment_purpose_generation_and_scope_mismatch_before_sdk() -> None:
    from app.execution.venues.lighter_sdk_transport import (
        LighterSdkTransportError,
        build_lighter_mainnet_transport,
    )

    cases = (
        (_credentials(environment="testnet"), _scope()),
        (_credentials(purpose="read"), _scope()),
        (_credentials(generation_id=None), _scope()),
        (_credentials(generation_id="gen-other"), _scope()),
        (_credentials(account_index=43), _scope()),
        (_credentials(api_key_index=8), _scope()),
    )
    for credentials, scope in cases:
        factory = _Factory()
        with pytest.raises(LighterSdkTransportError):
            build_lighter_mainnet_transport(
                credentials,
                canary_scope=scope,
                signer_client_factory=factory,
            )
        assert factory.calls == []


def test_mainnet_scope_rejects_malformed_snapshot_or_generation() -> None:
    from app.execution.venues.lighter_sdk_transport import (
        LighterMainnetCanaryScope,
        LighterSdkTransportError,
    )

    for kwargs in (
        {
            "snapshot_hash": "bad",
            "credential_generation_id": _GENERATION_ID,
            "account_index": 42,
            "api_key_index": 7,
        },
        {
            "snapshot_hash": _SNAPSHOT_HASH,
            "credential_generation_id": "",
            "account_index": 42,
            "api_key_index": 7,
        },
    ):
        with pytest.raises(LighterSdkTransportError):
            LighterMainnetCanaryScope(**kwargs)


def test_mainnet_transport_keeps_existing_explicit_nonce_and_sanitized_sdk_boundary() -> None:
    from app.execution.venues.lighter_sdk_transport import (
        LighterSdkTransportError,
        build_lighter_mainnet_transport,
    )

    factory = _Factory()
    transport = build_lighter_mainnet_transport(
        _credentials(), canary_scope=_scope(), signer_client_factory=factory
    )

    assert transport.next_nonce() == 321
    with pytest.raises(LighterSdkTransportError, match="explicit nonce"):
        transport.cancel_order(
            market_index=1,
            order_index=123,
            skip_nonce=0,
            nonce=321,
            api_key_index=7,
        )
