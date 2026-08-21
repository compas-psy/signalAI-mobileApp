"""Official Lighter SDK transport pinned to testnet for SAI-076.

This module is the first provider-I/O implementation behind the transport seam
introduced by SAI-070 and admitted by SAI-075. It is deliberately narrow: only
testnet trade credentials can construct it, every signed action must use an
explicit replay-safe nonce, and it exposes no LIVE/mainnet factory.
"""

from __future__ import annotations

import asyncio
import threading
import weakref
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .lighter import LighterEnvironment, lighter_base_url
from .lighter_actions import LighterActionAck
from .lighter_auth import LighterServerCredentials

_LIGHTER_TESTNET_BASE_URL = lighter_base_url(LighterEnvironment.TESTNET)
_LIGHTER_TESTNET_CHAIN_ID = 300
_SKIP_NONCE_ON = 1
_INT64_MAX = (1 << 63) - 1
_MAX_API_KEY_INDEX = 253

_T = TypeVar("_T")
SignerClientFactory = Callable[..., Any]


class LighterSdkTransportError(RuntimeError):
    """Sanitized fail-closed error from the official Lighter SDK boundary."""


def _valid_int(value: object, *, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= maximum
    )


class _SdkLoopThread:
    """Own one event loop for the entire lifetime of one SDK client.

    Lighter's generated REST client creates aiohttp.ClientSession during
    SignerClient construction. Construction and every async SDK operation must
    therefore stay on the same live event loop instead of using one asyncio.run
    per action.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._closed = False
        self._thread = threading.Thread(
            target=self._serve,
            name="lighter-sdk-loop",
            daemon=True,
        )
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
        future = asyncio.run_coroutine_threadsafe(awaitable, self._loop)
        return future.result()

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
        raise LighterSdkTransportError("Lighter testnet SDK client construction failed") from None


def _require_testnet_trade_credentials(credentials: LighterServerCredentials) -> None:
    if not isinstance(credentials, LighterServerCredentials):
        raise LighterSdkTransportError("Lighter testnet trade credentials are required")
    if credentials.environment != "testnet" or credentials.purpose != "trade":
        raise LighterSdkTransportError("Lighter testnet trade credentials are required")
    if not _valid_int(credentials.account_index, maximum=_INT64_MAX):
        raise LighterSdkTransportError("Lighter testnet trade credential scope is invalid")
    if not _valid_int(credentials.api_key_index, maximum=_MAX_API_KEY_INDEX):
        raise LighterSdkTransportError("Lighter testnet trade credential scope is invalid")
    if not isinstance(credentials.api_private_key, str) or not credentials.api_private_key:
        raise LighterSdkTransportError("Lighter testnet trade credentials are required")


class LighterSdkTestnetTransport:
    """Synchronous replay-safe adapter over the official async SignerClient."""

    base_url = _LIGHTER_TESTNET_BASE_URL
    chain_id = _LIGHTER_TESTNET_CHAIN_ID

    def __init__(
        self,
        *,
        client: Any,
        runner: _SdkLoopThread,
        account_index: int,
        api_key_index: int,
    ) -> None:
        if not _valid_int(account_index, maximum=_INT64_MAX):
            raise LighterSdkTransportError("Lighter testnet account scope is invalid")
        if not _valid_int(api_key_index, maximum=_MAX_API_KEY_INDEX):
            raise LighterSdkTransportError("Lighter testnet API-key scope is invalid")
        self._client = client
        self._runner = runner
        self._finalizer = weakref.finalize(
            self,
            _finalize_sdk_client,
            runner,
            client,
        )
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

    def close(self) -> None:
        """Close the provider HTTP session and its dedicated event loop."""

        self._finalizer()

    def check_client(self) -> str | None:
        try:
            error = self._runner.call(self._client.check_client)
        except Exception:
            raise LighterSdkTransportError("Lighter testnet credential check failed") from None
        if error is None:
            return None
        return "provider credential check failed"

    def next_nonce(self) -> int:
        try:
            response = self._runner.run(
                self._client.tx_api.next_nonce(self.account_index, self.api_key_index)
            )
        except Exception:
            raise LighterSdkTransportError("Lighter testnet nonce lookup failed") from None

        code = getattr(response, "code", 200)
        nonce = getattr(response, "nonce", None)
        if code != 200 or not _valid_int(nonce, maximum=_INT64_MAX):
            raise LighterSdkTransportError("Lighter testnet nonce lookup failed")
        return nonce

    def _validate_action_scope(self, kwargs: dict[str, Any]) -> None:
        if kwargs.get("skip_nonce") != _SKIP_NONCE_ON:
            raise LighterSdkTransportError("Lighter testnet action requires explicit nonce")
        if not _valid_int(kwargs.get("nonce"), maximum=_INT64_MAX):
            raise LighterSdkTransportError("Lighter testnet action requires explicit nonce")
        if kwargs.get("api_key_index") != self.api_key_index:
            raise LighterSdkTransportError("Lighter testnet action credential scope mismatch")

    @staticmethod
    def _normalize_ack(result: object, *, action: str) -> LighterActionAck:
        if not isinstance(result, tuple) or len(result) != 3:
            raise LighterSdkTransportError(f"Lighter testnet {action} failed")
        _signed_tx, response, provider_error = result
        if provider_error is not None or response is None:
            raise LighterSdkTransportError(f"Lighter testnet {action} failed")

        code = getattr(response, "code", None)
        tx_hash = getattr(response, "tx_hash", None)
        if not isinstance(code, int) or isinstance(code, bool):
            raise LighterSdkTransportError(f"Lighter testnet {action} failed")
        if not isinstance(tx_hash, str):
            tx_hash = ""
        message = None if code == 200 else "provider rejected action"
        return LighterActionAck(code=code, tx_hash=tx_hash, message=message)

    def create_order(self, **kwargs: Any) -> LighterActionAck:
        self._validate_action_scope(kwargs)
        try:
            result = self._runner.run(self._client.create_order(**kwargs))
        except Exception:
            raise LighterSdkTransportError("Lighter testnet create order failed") from None
        return self._normalize_ack(result, action="create order")

    def cancel_order(self, **kwargs: Any) -> LighterActionAck:
        self._validate_action_scope(kwargs)
        try:
            result = self._runner.run(self._client.cancel_order(**kwargs))
        except Exception:
            raise LighterSdkTransportError("Lighter testnet cancel order failed") from None
        return self._normalize_ack(result, action="cancel order")


def build_lighter_testnet_transport(
    credentials: LighterServerCredentials,
    *,
    signer_client_factory: SignerClientFactory | None = None,
) -> LighterSdkTestnetTransport:
    """Construct exactly one official-SDK transport for testnet trade scope."""

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


__all__ = [
    "LighterSdkTestnetTransport",
    "LighterSdkTransportError",
    "build_lighter_testnet_transport",
]
