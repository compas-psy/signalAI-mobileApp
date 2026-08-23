"""Authenticated thin-client endpoint for one bounded T-Invest Sandbox round trip.

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
from ...execution.venues.tinvest_sandbox_readiness import (
    current_tinvest_sandbox_context,
    record_tinvest_sandbox_roundtrip_proof,
    scoped_sandbox_diagnostic_key,
)
from ...execution.venues.tinvest_sandbox_smoke import (
    TInvestSandboxSmokeResult,
    run_tinvest_sandbox_smoke,
)
from ...schemas.common import ApiModel

router = APIRouter(prefix="/tinvest-sandbox", tags=["tinvest-sandbox"])

_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")


class TInvestSandboxSmokeResponse(ApiModel):
    round_trip_complete: bool
    symbol: str
    account_suffix: str
    buy_provider_order_id: str
    buy_execution_status: str
    buy_executed_lots: int
    sell_provider_order_id: str
    sell_execution_status: str
    sell_executed_lots: int
    position_flat: bool
    readiness_proof_id: str


def _idempotency_key(request: Request) -> str:
    value = request.headers.get("x-idempotency-key", "").strip()
    if _IDEMPOTENCY_RE.fullmatch(value) is None:
        raise HTTPException(400, "valid X-Idempotency-Key is required")
    return value


def _response(
    value: TInvestSandboxSmokeResult,
    *,
    readiness_proof_id: str,
) -> TInvestSandboxSmokeResponse:
    return TInvestSandboxSmokeResponse(
        round_trip_complete=value.round_trip_complete,
        symbol=value.symbol,
        account_suffix=value.account_suffix,
        buy_provider_order_id=value.buy_provider_order_id,
        buy_execution_status=value.buy_execution_status,
        buy_executed_lots=value.buy_executed_lots,
        sell_provider_order_id=value.sell_provider_order_id,
        sell_execution_status=value.sell_execution_status,
        sell_executed_lots=value.sell_executed_lots,
        position_flat=value.position_flat,
        readiness_proof_id=readiness_proof_id,
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
    client_key = _idempotency_key(request)
    try:
        context = current_tinvest_sandbox_context(db)
        diagnostic_key = scoped_sandbox_diagnostic_key(client_key, context)
        result = run_tinvest_sandbox_smoke(db, diagnostic_key=diagnostic_key)
    except TInvestProviderError as exc:
        if exc.code == "INVALID_REQUEST":
            raise HTTPException(400, "sandbox diagnostic request is invalid") from exc
        if exc.code in {"CREDENTIAL_MISSING", "CREDENTIAL_INVALID"}:
            raise HTTPException(409, "T-Invest Sandbox is not configured on the server") from exc
        if exc.code == "SERVER_PROVENANCE_MISSING":
            raise HTTPException(503, "server release provenance is unavailable") from exc
        if exc.code == "MARKET_UNAVAILABLE":
            raise HTTPException(409, "T-Invest Sandbox limit market is unavailable now") from exc
        if exc.code == "TRANSPORT":
            raise HTTPException(503, "T-Invest Sandbox provider is unavailable") from exc
        raise HTTPException(502, "T-Invest Sandbox provider rejected the diagnostic trade") from exc

    if not result.round_trip_complete:
        raise HTTPException(
            409,
            "T-Invest Sandbox round trip is incomplete "
            f"(buy={result.buy_executed_lots}, "
            f"sell={result.sell_executed_lots}, flat={result.position_flat})",
        )

    proof_id = record_tinvest_sandbox_roundtrip_proof(
        db,
        context=context,
        symbol=result.symbol,
        account_suffix=result.account_suffix,
        buy_order_id=result.buy_provider_order_id,
        buy_status=result.buy_execution_status,
        buy_executed_lots=result.buy_executed_lots,
        sell_order_id=result.sell_provider_order_id,
        sell_status=result.sell_execution_status,
        sell_executed_lots=result.sell_executed_lots,
        position_flat=result.position_flat,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return _response(result, readiness_proof_id=proof_id)


__all__ = ["router"]
