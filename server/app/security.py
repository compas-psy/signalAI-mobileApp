"""Аутентификация мобильного клиента по токену устройства.

APK передаёт ``Authorization: Bearer <token>``. Прежде сервер этот заголовок
не проверял, поэтому любой знающий публичный домен мог читать идеи, запускать
сканирование и переключать kill switch.

Все запросы к ``/api/*`` fail-closed: если токен не настроен, бизнес-API не
работает, а не становится публичным молча. ``/health`` находится вне этого
префикса и остаётся доступен для мониторинга без секрета.
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

        # Нельзя обходить проверку по request.client.host. Reverse proxy на том
        # же VPS подключается к Uvicorn с 127.0.0.1, поэтому такой bypass сделал
        # бы внешние запросы неотличимыми от локальных и снова открыл бы API.
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
