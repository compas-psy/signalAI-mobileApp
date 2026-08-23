"""Fixed-host HTTP transport for T-Invest Sandbox only.

The transport intentionally has no configurable base URL.  A server-owned
sandbox bearer can therefore never be redirected to the live T-Invest host by
request data, application configuration, or a caller-provided URL.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from ...integration_secrets import load_secret
from .tinvest import TInvestProviderError, TInvestTransport

TINVEST_SANDBOX_REST_BASE = "https://sandbox-invest-public-api.tbank.ru/rest"
_NAMESPACE = "tinkoff.public.invest.api.contract.v1"
_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_SERVICE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*Service$")
_METHOD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")

_Opener = Callable[..., Any]


class TInvestSandboxHttpTransport(TInvestTransport):
    """Minimal JSON-over-HTTPS bridge bound permanently to T-Invest Sandbox."""

    __slots__ = ("_token", "_opener")

    def __init__(self, *, token: str, opener: _Opener | None = None) -> None:
        cleaned = token.strip() if isinstance(token, str) else ""
        if not cleaned or "\n" in cleaned or "\r" in cleaned or "\x00" in cleaned:
            raise TInvestProviderError(
                code="CREDENTIAL_INVALID",
                message="sandbox credential is invalid",
            )
        self._token = cleaned
        self._opener = opener or urllib.request.urlopen

    def __repr__(self) -> str:
        return "TInvestSandboxHttpTransport(host='sandbox-invest-public-api.tbank.ru')"

    def call(
        self,
        service: str,
        method: str,
        body: dict[str, object],
    ) -> Mapping[str, object]:
        if (
            not isinstance(service, str)
            or _SERVICE_RE.fullmatch(service) is None
            or not isinstance(method, str)
            or _METHOD_RE.fullmatch(method) is None
            or not isinstance(body, dict)
        ):
            raise TInvestProviderError(
                code="INVALID_REQUEST",
                message="sandbox provider request is invalid",
            )

        try:
            payload = json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise TInvestProviderError(
                code="INVALID_REQUEST",
                message="sandbox provider request is invalid",
            ) from exc

        url = f"{TINVEST_SANDBOX_REST_BASE}/{_NAMESPACE}.{service}/{method}"
        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with self._opener(request, timeout=_TIMEOUT_SECONDS) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise TInvestProviderError(
                code=f"HTTP_{int(exc.code)}",
                message="sandbox provider rejected request",
                not_found=int(exc.code) == 404,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TInvestProviderError(
                code="TRANSPORT",
                message="sandbox provider is unavailable",
            ) from exc
        except TInvestProviderError:
            raise
        except Exception as exc:
            # The raw transport exception may contain proxy URLs, headers or
            # other environment detail.  Preserve the cause internally but
            # never echo it through the public execution error.
            raise TInvestProviderError(
                code="TRANSPORT",
                message="sandbox provider request failed",
            ) from exc

        if len(raw) > _MAX_RESPONSE_BYTES:
            raise TInvestProviderError(
                code="INVALID_RESPONSE",
                message="sandbox provider response is too large",
            )
        try:
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TInvestProviderError(
                code="INVALID_RESPONSE",
                message="sandbox provider response is invalid",
            ) from exc
        if not isinstance(decoded, dict):
            raise TInvestProviderError(
                code="INVALID_RESPONSE",
                message="sandbox provider response is not an object",
            )
        return decoded


def build_tinvest_sandbox_transport(
    session: Session,
    *,
    opener: _Opener | None = None,
) -> TInvestSandboxHttpTransport:
    """Load exactly the server-owned sandbox credential and build transport."""

    secret = load_secret(session, "tinvest_sandbox_trade")
    if secret is None:
        raise TInvestProviderError(
            code="CREDENTIAL_MISSING",
            message="T-Invest Sandbox is not configured on the server",
        )
    if set(secret) != {"token"}:
        raise TInvestProviderError(
            code="CREDENTIAL_INVALID",
            message="T-Invest Sandbox credential is invalid",
        )
    return TInvestSandboxHttpTransport(token=secret["token"], opener=opener)


__all__ = [
    "TINVEST_SANDBOX_REST_BASE",
    "TInvestSandboxHttpTransport",
    "build_tinvest_sandbox_transport",
]
