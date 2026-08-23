"""Device-authenticated, server-owned T-Invest Sandbox acceptance route."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ...db import get_db
from ...device_auth import require_device_token
from ...execution.venues.tinvest import TInvestProviderError
from ...execution.venues.tinvest_sandbox_readiness import (
    current_tinvest_sandbox_context,
    record_tinvest_sandbox_roundtrip_proof,
    scoped_sandbox_diagnostic_key,
)
from ...execution.venues.tinvest_sandbox_smoke import run_tinvest_sandbox_smoke

router = APIRouter(
    prefix="/api/v1/tinvest-sandbox",
    tags=["tinvest-sandbox"],
    dependencies=[Depends(require_device_token)],
)
Db = Annotated[Session, Depends(get_db)]


class TInvestSandboxSmokeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_trip_complete: bool
    symbol: str
    account_suffix: str
    buy_provider_order_id: str
    buy_execution_status: str
    buy_executed_lots: int = Field(ge=0)
    sell_provider_order_id: str
    sell_execution_status: str
    sell_executed_lots: int = Field(ge=0)
    position_flat: bool
    readiness_proof_id: str


def _provider_http_error(exc: TInvestProviderError) -> HTTPException:
    if exc.code == "CREDENTIAL_MISSING":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="T-Invest Sandbox credential is not configured on server",
        )
    if exc.code == "SERVER_PROVENANCE_MISSING":
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="server release provenance is unavailable",
        )
    if exc.code == "MARKET_UNAVAILABLE":
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no allowed T-Invest Sandbox instrument is limit-tradeable now",
        )
    if exc.code in {"PROVIDER_AUTH", "PROVIDER_FORBIDDEN"}:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="T-Invest Sandbox credential was rejected by provider",
        )
    if exc.code == "RATE_LIMITED":
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="T-Invest Sandbox provider is rate limited",
        )
    if exc.code in {"INVALID_REQUEST", "NOT_FOUND"}:
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="T-Invest Sandbox diagnostic request is not available",
        )
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="T-Invest Sandbox provider did not confirm the diagnostic transaction",
    )


@router.post("/smoke", response_model=TInvestSandboxSmokeResponse)
def smoke_tinvest_sandbox(
    db: Db,
    x_idempotency_key: Annotated[
        str,
        Header(alias="X-Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
    ],
) -> TInvestSandboxSmokeResponse:
    try:
        context = current_tinvest_sandbox_context(db)
        diagnostic_key = scoped_sandbox_diagnostic_key(x_idempotency_key, context)
        result = run_tinvest_sandbox_smoke(db, diagnostic_key=diagnostic_key)
    except TInvestProviderError as exc:
        raise _provider_http_error(exc) from exc

    if not result.round_trip_complete:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "T-Invest Sandbox round trip is incomplete "
                f"(buy={result.buy_executed_lots}, "
                f"sell={result.sell_executed_lots}, flat={result.position_flat})"
            ),
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

    return TInvestSandboxSmokeResponse(
        round_trip_complete=True,
        symbol=result.symbol,
        account_suffix=result.account_suffix,
        buy_provider_order_id=result.buy_provider_order_id,
        buy_execution_status=result.buy_execution_status,
        buy_executed_lots=result.buy_executed_lots,
        sell_provider_order_id=result.sell_provider_order_id,
        sell_execution_status=result.sell_execution_status,
        sell_executed_lots=result.sell_executed_lots,
        position_flat=result.position_flat,
        readiness_proof_id=proof_id,
    )
