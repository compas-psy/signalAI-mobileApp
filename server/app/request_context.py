"""Secret-free request correlation for owner-facing runtime diagnostics."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

_REQUEST_ID_HEADER = "X-Request-ID"


def _request_id(value: str | None) -> str:
    if value:
        try:
            UUID(value)
        except (ValueError, AttributeError):
            pass
        else:
            return value
    return str(uuid4())


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Adds one UUID correlation id without inspecting body or credentials."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = _request_id(request.headers.get(_REQUEST_ID_HEADER))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response


__all__ = ["RequestIdMiddleware"]
