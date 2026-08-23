"""Personal broker integrations.

All routes are protected by DeviceTokenMiddleware. Secret values are write-only:
GET returns only configured/not-configured metadata, never credentials.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...db import get_db
from ...integration_secrets import (
    BY_SLOT,
    SPECS,
    IntegrationSpec,
    configured_slots,
    delete_secret,
    save_secret,
    validate_values,
)

router = APIRouter(prefix="/integrations", tags=["integrations"])
_LIGHTER_LIVE_TRADE_SLOT = "lighter_trade"
_OWNER_STEP_UP_LIVE_TRADE_SLOTS = frozenset(
    {"tinvest_trade", "bybit_trade", _LIGHTER_LIVE_TRADE_SLOT}
)
_LIGHTER_LIVE_STEP_UP_REQUIRED = "LIGHTER_LIVE_STEP_UP_REQUIRED"
_LIVE_TRADE_STEP_UP_REQUIRED = "LIVE_TRADE_CREDENTIAL_STEP_UP_REQUIRED"


class IntegrationUpdate(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


class IntegrationStatus(BaseModel):
    slot: str
    venue: str
    title: str
    purpose: str
    environment: str
    fields: list[str]
    required: bool
    configured: bool
    updated_at: datetime | None = None


def _view(spec: IntegrationSpec, present: dict[str, datetime]) -> IntegrationStatus:
    return IntegrationStatus(
        slot=spec.slot,
        venue=spec.venue,
        title=spec.title,
        purpose=spec.purpose,
        environment=spec.environment,
        fields=list(spec.fields),
        required=spec.required,
        configured=spec.slot in present,
        updated_at=present.get(spec.slot),
    )


def _live_trade_step_up_blocker(slot: str) -> str:
    if slot == _LIGHTER_LIVE_TRADE_SLOT:
        return _LIGHTER_LIVE_STEP_UP_REQUIRED
    return _LIVE_TRADE_STEP_UP_REQUIRED


@router.get("", response_model=list[IntegrationStatus])
def list_integrations(db: Session = Depends(get_db)) -> list[IntegrationStatus]:
    present = configured_slots(db)
    return [_view(spec, present) for spec in SPECS]


@router.put("/{slot}", response_model=IntegrationStatus)
def put_integration(
    slot: str,
    payload: IntegrationUpdate,
    db: Session = Depends(get_db),
) -> IntegrationStatus:
    spec = BY_SLOT.get(slot)
    if spec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="неизвестный слот интеграции")

    if slot in _OWNER_STEP_UP_LIVE_TRADE_SLOTS:
        # Preserve deterministic input-validation semantics, but never let an
        # ordinary enrolled-device bearer provision or rotate live trading
        # authority. A future accepted owner-sensitive step-up flow may call
        # the server-side vault only after its exact challenge is verified.
        try:
            validate_values(spec, payload.values)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_live_trade_step_up_blocker(slot),
        )

    try:
        updated = save_secret(db, spec, payload.values)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return IntegrationStatus(
        slot=spec.slot,
        venue=spec.venue,
        title=spec.title,
        purpose=spec.purpose,
        environment=spec.environment,
        fields=list(spec.fields),
        required=spec.required,
        configured=True,
        updated_at=updated,
    )


@router.delete("/{slot}")
def remove_integration(slot: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    if slot not in BY_SLOT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="неизвестный слот интеграции")
    if slot in _OWNER_STEP_UP_LIVE_TRADE_SLOTS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_live_trade_step_up_blocker(slot),
        )
    delete_secret(db, slot)
    return {"ok": True}
