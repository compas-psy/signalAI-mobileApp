"""Fail-closed authentication for enrolled owner devices.

``SIGNALAI_DEVICE_TOKEN`` is a bootstrap secret for the exact pairing path;
all other ``/api/*`` routes accept only a non-revoked device credential.
``/health`` remains outside this prefix for unauthenticated monitoring.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from .db import session_scope
from .device_enrollment import authenticate_active_device

_PAIRING_PATH = "/api/v1/device-enrollment/pair"
_REVOCATION_PATH = "/api/v1/device-enrollment/revoke"
_ACTIVE_ONLY_POLICY = "active_only"


def _device_auth_policy() -> str:
    """Return the only supported transition policy, or fail closed.

    There is deliberately no compatibility mode where the bootstrap secret
    continues to authorize business routes.  An unrecognised deployment value
    is an outage (503), not a silent privilege downgrade.
    """
    policy = os.environ.get("SIGNALAI_DEVICE_AUTH_POLICY", _ACTIVE_ONLY_POLICY).strip()
    if policy != _ACTIVE_ONLY_POLICY:
        raise RuntimeError("unsupported device authentication policy")
    return policy


def _authenticate_from_database(token: str):
    with session_scope() as session:
        return authenticate_active_device(session, token)


class DeviceTokenMiddleware(BaseHTTPMiddleware):
    """Accept only enrolled, non-revoked device credentials on business APIs."""

    def __init__(
        self,
        app,
        *,
        authenticate: Callable[[str], object | None] = _authenticate_from_database,
    ) -> None:
        super().__init__(app)
        self._authenticate = authenticate

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        try:
            _device_auth_policy()
        except RuntimeError:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "message": "политика авторизации устройства недоступна; API закрыт"
                    }
                },
            )

        # Bootstrap token can reach exactly one endpoint and never authorizes
        # a business request. Pairing authenticates its two-part capability
        # inside the route after the global policy is confirmed fail-closed.
        if request.method == "POST" and request.url.path == _PAIRING_PATH:
            return await call_next(request)

        # Нельзя обходить проверку по request.client.host. Reverse proxy на том
        # же VPS подключается к Uvicorn с 127.0.0.1, поэтому такой bypass сделал
        # бы внешние запросы неотличимыми от локальных и снова открыл бы API.
        authorization = request.headers.get("authorization", "")
        scheme, _, supplied = authorization.partition(" ")
        # Self-revocation is intentionally idempotent: a phone that crashed
        # after the server revocation can safely clear its local Keystore only
        # after receiving an explicit already-revoked outcome.  This narrow
        # exception admits no business operation and the route verifies the
        # exact bearer against the verifier table itself.
        if request.method == "POST" and request.url.path == _REVOCATION_PATH:
            if scheme.lower() == "bearer" and supplied.strip():
                request.state.device_revocation_bearer = supplied.strip()
                return await call_next(request)
            return JSONResponse(
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
                content={"error": {"message": "active device token is required"}},
            )

        credential = None
        if scheme.lower() == "bearer" and supplied:
            try:
                credential = self._authenticate(supplied.strip())
            except Exception:
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": {
                            "message": "проверка устройства недоступна; API закрыт"
                        }
                    },
                )
        if credential is None:
            return JSONResponse(
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
                content={
                    "error": {
                        "message": "устройство не авторизовано или токен устарел",
                    }
                },
            )

        request.state.device_credential_id = getattr(credential, "id", None)
        request.state.device_id = getattr(credential, "device_id", None)
        request.state.device_generation = getattr(credential, "generation", None)
        request.state.device_authenticated = True

        return await call_next(request)
