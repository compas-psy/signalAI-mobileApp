"""Official Lighter SDK transports behind explicit credential/network scopes.

Testnet remains the only generally constructible trading transport. The
mainnet constructor added for the Canary boundary is narrower: it requires a
server-side live/trade credential whose opaque generation, account and API-key
identity exactly matches an immutable Canary scope. Merely constructing that
transport is not owner activation and never grants Scaled LIVE authority.
"""

from __future__ import annotations

import asyncio
import re
import threading
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from .lighter import LighterEnvironment, lighter_base_url
from .lighter_actions import LighterActionAck
from .lighter_auth import LighterServerCredentials

_LIGHTER_TESTNET_BASE_URL = lighter_base_url(LighterEnvironment.TESTNET)
_LIGHTER_MAINNET_BASE_URL = lighter_base_url(LighterEnvironment.MAINNET)
_LIGHTER_TESTNET_CHAIN_ID = 300
_SKIP_NONCE_ON = 1
_INT64_MAX = (1 << 63) - 1
_MAX_API_KEY_INDEX = 253
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

_T = TypeVar("_T")
SignerClientFactory = Callable[..., Any]


class LighterSdkTransportError(RuntimeError):
    """Sanitized fail-closed error from the official Lighter SDK boundary."""


def _valid_int(value: object, *, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum


@dataclass(frozen=True, slots=True)
class LighterMainnetCanaryScope:
    """Non-secret immutable identity required before constructing mainnet SDK I/O."""

    snapshot_hash: str
    credential_generation_id: str
    account_index: int
    api_key_index: int

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_hash, str) or _HEX64.fullmatch(self.snapshot_hash) is None:
            raise LighterSdkTransportError("Lighter Canary snapshot scope is invalid")
        generation = self.credential_generation_id
        if (
            not isinstance(generation, str)
            or not generation.strip()
            or generation != generation.strip()
            or len(generation) > 128
            or any(character in generation for character in "\r\n\x00")
        ):
            raise LighterSdkTransportError("Lighter Canary credential generation scope is invalid")
        if not _valid_int(self.account_index, maximum=_INT64_MAX):
            raise LighterSdkTransportError("Lighter Canary account scope is invalid")
        if not _valid_int(self.api_key_index, maximum=_MAX_API_KEY_INDEX):
            raise LighterSdkTransportError("Lighter Canary API-key scope is invalid")


class _SdkLoopThread:
    """Own one event loop for the entire lifetime of one SDK client."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._closed = False
        self._thread = threading.Thread(target=self._serve, name="lighter-sdk-loop", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()
        self._loop.close()

    def run(self, awaitable: Awaitable[_T]) -> _T:
        if self._closed:
            raise RuntimeError("Lighter SDK event loop is closed")
        return asyncio.run_coroutine_threadsafe(awaitable, self._loop).result()

    def call(self, function: Callable[[], _T]) -> _T:
        async def invoke() -> _T:
            return function()

        return self.run(invoke())

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:
            return
        if threading.current_thread() is not self._thread:
            self._thread.join()


def _finalize_sdk_client(runner: _SdkLoopThread, client: Any) -> None:
    try:
        close = getattr(client, "close", None)
        if callable(close):
            result = runner.call(close)
            if isinstance(result, Awaitable):
                runner.run(result)
    except Exception:
        pass
    finally:
        runner.close()


def _official_signer_client_factory(**kwargs: Any) -> Any:
    try:
        from lighter import SignerClient
    except Exception:
        raise LighterSdkTransportError("official Lighter SDK is unavailable") from None
    try:
        return SignerClient(**kwargs)
    except Exception:
        raise LighterSdkTransportError("Lighter SDK client construction failed") from None


def _require_trade_credentials(
    credentials: LighterServerCredentials,
    *,
    environment: str,
    label: str,
) -> None:
    if not isinstance(credentials, LighterServerCredentials):
        raise LighterSdkTransportError(f"{label} trade credentials are required")
    if credentials.environment != environment or credentials.purpose != "trade":
        raise LighterSdkTransportError(f"{label} trade credentials are required")
    if not _valid_int(credentials.account_index, maximum=_INT64_MAX):
        raise LighterSdkTransportError(f"{label} trade credential scope is invalid")
    if not _valid_int(credentials.api_key_index, maximum=_MAX_API_KEY_INDEX):
        raise LighterSdkTransportError(f"{label} trade credential scope is invalid")
    if not isinstance(credentials.api_private_key, str) or not credentials.api_private_key:
        raise LighterSdkTransportError(f"{label} trade credentials are required")


def _require_testnet_trade_credentials(credentials: LighterServerCredentials) -> None:
    _require_trade_credentials(credentials, environment="testnet", label="Lighter testnet")


def _require_mainnet_canary_credentials(
    credentials: LighterServerCredentials,
    scope: LighterMainnetCanaryScope,
) -> None:
    _require_trade_credentials(credentials, environment="live", label="Lighter mainnet")
    generation_id = credentials.credential_generation_id
    if not isinstance(generation_id, str) or not generation_id.strip():
        raise LighterSdkTransportError("Lighter mainnet credential generation is required")
    if (
        generation_id != scope.credential_generation_id
        or credentials.account_index != scope.account_index
        or credentials.api_key_index != scope.api_key_index
    ):
        raise LighterSdkTransportError("Lighter mainnet credential scope mismatch")


class LighterSdkTestnetTransport:
    """Synchronous replay-safe adapter over the official async SignerClient."""

    base_url = _LIGHTER_TESTNET_BASE_URL
    chain_id: int | None = _LIGHTER_TESTNET_CHAIN_ID
    _transport_label = "Lighter testnet"

    def __init__(self, *, client: Any, runner: _SdkLoopThread, account_index: int, api_key_index: int) -> None:
        if not _valid_int(account_index, maximum=_INT64_MAX):
            raise LighterSdkTransportError(f"{self._transport_label} account scope is invalid")
        if not _valid_int(api_key_index, maximum=_MAX_API_KEY_INDEX):
            raise LighterSdkTransportError(f"{self._transport_label} API-key scope is invalid")
        self._client = client
        self._runner = runner
        self._finalizer = weakref.finalize(self, _finalize_sdk_client, runner, client)
        self.account_index = account_index
        self.api_key_index = api_key_index

    def __repr__(self) -> str:
        return (
            "LighterSdkTestnetTransport("
            f"base_url={self.base_url!r}, chain_id={self.chain_id}, "
            f"account_index={self.account_index}, api_key_index={self.api_key_index})"
        )

    @property
    def eligible_for_live(self) -> bool:
        return False

    @property
    def eligible_for_canary_transport(self) -> bool:
        return False

    def close(self) -> None:
        self._finalizer()

    def check_client(self) -> str | None:
        try:
            error = self._runner.call(self._client.check_client)
        except Exception:
            raise LighterSdkTransportError(f"{self._transport_label} credential check failed") from None
        if error is None:
            return None
        return "provider credential check failed"

    def next_nonce(self) -> int:
        try:
            response = self._runner.run(self._client.tx_api.next_nonce(self.account_index, self.api_key_index))
        except Exception:
            raise LighterSdkTransportError(f"{self._transport_label} nonce lookup failed") from None
        code = getattr(response, "code", 200)
        nonce = getattr(response, "nonce", None)
        if code != 200 or not _valid_int(nonce, maximum=_INT64_MAX):
            raise LighterSdkTransportError(f"{self._transport_label} nonce lookup failed")
        return nonce

    def _validate_action_scope(self, kwargs: dict[str, Any]) -> None:
        if kwargs.get("skip_nonce") != _SKIP_NONCE_ON or not _valid_int(kwargs.get("nonce"), maximum=_INT64_MAX):
            raise LighterSdkTransportError(f"{self._transport_label} action requires explicit nonce")
        if kwargs.get("api_key_index") != self.api_key_index:
            raise LighterSdkTransportError(f"{self._transport_label} action credential scope mismatch")

    def _normalize_ack(self, result: object, *, action: str) -> LighterActionAck:
        if not isinstance(result, tuple) or len(result) != 3:
            raise LighterSdkTransportError(f"{self._transport_label} {action} failed")
        _signed_tx, response, provider_error = result
        if provider_error is not None or response is None:
            raise LighterSdkTransportError(f"{self._transport_label} {action} failed")
        code = getattr(response, "code", None)
        tx_hash = getattr(response, "tx_hash", None)
        if not isinstance(code, int) or isinstance(code, bool):
            raise LighterSdkTransportError(f"{self._transport_label} {action} failed")
        if not isinstance(tx_hash, str):
            tx_hash = ""
        return LighterActionAck(code=code, tx_hash=tx_hash, message=None if code == 200 else "provider rejected action")

    def create_order(self, **kwargs: Any) -> LighterActionAck:
        self._validate_action_scope(kwargs)
        try:
            result = self._runner.run(self._client.create_order(**kwargs))
        except Exception:
            raise LighterSdkTransportError(f"{self._transport_label} create order failed") from None
        return self._normalize_ack(result, action="create order")

    def cancel_order(self, **kwargs: Any) -> LighterActionAck:
        self._validate_action_scope(kwargs)
        try:
            result = self._runner.run(self._client.cancel_order(**kwargs))
        except Exception:
            raise LighterSdkTransportError(f"{self._transport_label} cancel order failed") from None
        return self._normalize_ack(result, action="cancel order")


class LighterSdkMainnetCanaryTransport(LighterSdkTestnetTransport):
    """Mainnet SDK transport bound to one exact non-secret Canary identity."""

    base_url = _LIGHTER_MAINNET_BASE_URL
    chain_id = None
    _transport_label = "Lighter mainnet"

    def __init__(
        self,
        *,
        client: Any,
        runner: _SdkLoopThread,
        account_index: int,
        api_key_index: int,
        credential_generation_id: str,
        canary_snapshot_hash: str,
    ) -> None:
        super().__init__(client=client, runner=runner, account_index=account_index, api_key_index=api_key_index)
        self.credential_generation_id = credential_generation_id
        self.canary_snapshot_hash = canary_snapshot_hash

    def __repr__(self) -> str:
        return (
            "LighterSdkMainnetCanaryTransport("
            f"base_url={self.base_url!r}, account_index={self.account_index}, "
            f"api_key_index={self.api_key_index}, credential_generation_id={self.credential_generation_id!r}, "
            f"canary_snapshot_hash={self.canary_snapshot_hash!r}, eligible_for_live=False)"
        )

    @property
    def eligible_for_canary_transport(self) -> bool:
        return True


def build_lighter_testnet_transport(
    credentials: LighterServerCredentials,
    *,
    signer_client_factory: SignerClientFactory | None = None,
) -> LighterSdkTestnetTransport:
    _require_testnet_trade_credentials(credentials)
    factory = signer_client_factory or _official_signer_client_factory
    runner = _SdkLoopThread()
    try:
        client = runner.call(
            lambda: factory(
                url=_LIGHTER_TESTNET_BASE_URL,
                account_index=credentials.account_index,
                api_private_keys={credentials.api_key_index: credentials.api_private_key},
                chain_id=_LIGHTER_TESTNET_CHAIN_ID,
            )
        )
    except Exception:
        runner.close()
        raise LighterSdkTransportError("Lighter testnet SDK client construction failed") from None
    return LighterSdkTestnetTransport(
        client=client,
        runner=runner,
        account_index=credentials.account_index,
        api_key_index=credentials.api_key_index,
    )


def build_lighter_mainnet_transport(
    credentials: LighterServerCredentials,
    *,
    canary_scope: LighterMainnetCanaryScope,
    signer_client_factory: SignerClientFactory | None = None,
) -> LighterSdkMainnetCanaryTransport:
    """Construct mainnet SDK I/O only for an exact immutable Canary scope.

    Official Lighter documentation constructs the mainnet ``SignerClient`` with
    URL, account index and API-key map. No undocumented mainnet chain id is
    guessed. This factory is intentionally not wired to a worker or API and
    ``eligible_for_live`` remains false.
    """

    if not isinstance(canary_scope, LighterMainnetCanaryScope):
        raise LighterSdkTransportError("Lighter Canary scope is required")
    _require_mainnet_canary_credentials(credentials, canary_scope)
    factory = signer_client_factory or _official_signer_client_factory
    runner = _SdkLoopThread()
    try:
        client = runner.call(
            lambda: factory(
                url=_LIGHTER_MAINNET_BASE_URL,
                account_index=credentials.account_index,
                api_private_keys={credentials.api_key_index: credentials.api_private_key},
            )
        )
    except Exception:
        runner.close()
        raise LighterSdkTransportError("Lighter mainnet SDK client construction failed") from None
    return LighterSdkMainnetCanaryTransport(
        client=client,
        runner=runner,
        account_index=credentials.account_index,
        api_key_index=credentials.api_key_index,
        credential_generation_id=canary_scope.credential_generation_id,
        canary_snapshot_hash=canary_scope.snapshot_hash,
    )


__all__ = [
    "LighterMainnetCanaryScope",
    "LighterSdkMainnetCanaryTransport",
    "LighterSdkTestnetTransport",
    "LighterSdkTransportError",
    "build_lighter_mainnet_transport",
    "build_lighter_testnet_transport",
]
