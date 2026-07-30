"""Аутентификация мобильного клиента по токену устройства.

APK уже передаёт ``Authorization: Bearer <token>``, но прежде сервер этот
заголовок не проверял. В результате любой, кто знал публичный домен, мог
читать идеи, запускать сканирование и переключать kill switch.

Локальный доступ с loopback оставлен для health/debug-команд на самом VPS.
Все внешние запросы к ``/api/*`` fail-closed: если токен не настроен, API не
работает, а не становится публичным молча.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response


class DeviceTokenMiddleware(BaseHTTPMiddleware):
    """Защищает все бизнес-эндпоинты единым токеном устройства."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        # Команды диагностики, выполненные непосредственно на VPS, не требуют
        # вытаскивать токен из root-only /etc/signalai/.env в командную строку.
        client_host = request.client.host if request.client else ""
        if client_host in {"127.0.0.1", "::1"}:
            return await call_next(request)

        expected = os.environ.get("SIGNALAI_DEVICE_TOKEN", "").strip()
        if not expected:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "message": (
                            "токен устройства не настроен на сервере; "
                            "внешний API закрыт"
                        )
                    }
                },
            )

        authorization = request.headers.get("authorization", "")
        scheme, _, supplied = authorization.partition(" ")
        valid = (
            scheme.lower() == "bearer"
            and bool(supplied)
            and hmac.compare_digest(supplied.strip(), expected)
        )
        if not valid:
            return JSONResponse(
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
                content={
                    "error": {
                        "message": "устройство не авторизовано или токен устарел"
                    }
                },
            )

        return await call_next(request)
