"""Authenticated thin-client endpoint for one bounded T-Invest Sandbox smoke.

The route accepts only an idempotency key. Symbol, quantity, account funding,
provider host and sandbox-only semantics are fixed inside the server service.
No broker credential or raw provider response is returned to the device.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ...db import get_db
from ...execution.venues.tinvest import TInvestProviderError
from ...execution.venues.tinvest_sandbox_smoke import (
    TInvestSandboxSmokeResult,
    run_tinvest_sandbox_smoke,
)
from ...schemas.common import ApiModel

router = APIRouter(prefix="/tinvest-sandbox", tags=["tinvest-sandbox"])

_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")


class TInvestSandboxSmokeResponse(ApiModel):
    filled: bool
    symbol: str
    account_suffix: str
    provider_order_id: str
    execution_status: str
    executed_lots: int


def _idempotency_key(request: Request) -> str:
    value = request.headers.get("x-idempotency-key", "").strip()
    if _IDEMPOTENCY_RE.fullmatch(value) is None:
        raise HTTPException(400, "valid X-Idempotency-Key is required")
    return value


def _response(value: TInvestSandboxSmokeResult) -> TInvestSandboxSmokeResponse:
    return TInvestSandboxSmokeResponse(
        filled=value.filled,
        symbol=value.symbol,
        account_suffix=value.account_suffix,
        provider_order_id=value.provider_order_id,
        execution_status=value.execution_status,
        executed_lots=value.executed_lots,
    )


@router.post("/smoke", response_model=TInvestSandboxSmokeResponse)
def smoke(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TInvestSandboxSmokeResponse:
    # DeviceTokenMiddleware already authenticates every /api/* business route.
    # Keep a local fail-closed assertion so this endpoint cannot accidentally
    # become usable if router/middleware composition changes in the future.
    if getattr(request.state, "device_credential_id", None) is None:
        raise HTTPException(401, "active device token is required")
    key = _idempotency_key(request)
    try:
        result = run_tinvest_sandbox_smoke(db, diagnostic_key=key)
    except TInvestProviderError as exc:
        if exc.code == "INVALID_REQUEST":
            raise HTTPException(400, "sandbox diagnostic request is invalid") from exc
        if exc.code in {"CREDENTIAL_MISSING", "CREDENTIAL_INVALID"}:
            raise HTTPException(409, "T-Invest Sandbox is not configured on the server") from exc
        if exc.code == "MARKET_UNAVAILABLE":
            raise HTTPException(409, "T-Invest Sandbox market is unavailable now") from exc
        if exc.code == "TRANSPORT":
            raise HTTPException(503, "T-Invest Sandbox provider is unavailable") from exc
        raise HTTPException(502, "T-Invest Sandbox provider rejected the diagnostic trade") from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return _response(result)


__all__ = ["router"]
