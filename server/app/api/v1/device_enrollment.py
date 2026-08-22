"""One-time bootstrap pairing and active-device credential lifecycle."""
from __future__ import annotations

import hmac
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy.orm import Session

from ...db import get_db
from ...device_enrollment import (
    BootstrapPairingSession,
    DeviceEnrollmentConflict,
    DeviceEnrollmentError,
    DeviceEnrollmentReplay,
    IssuedDeviceCredential,
    bootstrap_pairing_session_config,
    pair_device,
    revoke_device_token,
    revoke_lost_device,
    rotate_device,
)
from ...schemas.common import ApiModel

router = APIRouter(prefix="/device-enrollment", tags=["device-enrollment"])


class PairDeviceRequest(ApiModel):
    device_id: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    metadata: dict[str, str] = Field(default_factory=dict)


class IssuedDeviceToken(ApiModel):
    device_id: str
    generation: int
    device_token: str
    issued_at: datetime


class LostDeviceRevokeRequest(ApiModel):
    device_id: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    generation: int = Field(ge=1)


class RevocationOutcome(ApiModel):
    status: str


def _response(result: IssuedDeviceCredential, response: Response) -> IssuedDeviceToken:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return IssuedDeviceToken(
        device_id=result.device_id,
        generation=result.generation,
        device_token=result.token,
        issued_at=result.issued_at,
    )


def _idempotency_key(request: Request) -> str:
    key = request.headers.get("x-idempotency-key", "")
    if not key:
        raise HTTPException(400, "X-Idempotency-Key is required")
    return key


def _bootstrap_pairing_authorized(request: Request) -> BootstrapPairingSession:
    expected = os.environ.get("SIGNALAI_DEVICE_TOKEN", "").strip()
    authorization = request.headers.get("authorization", "")
    scheme, _, supplied = authorization.partition(" ")
    if not expected:
        raise HTTPException(503, "device bootstrap pairing is not configured")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.strip(), expected):
        raise HTTPException(401, "bootstrap pairing is not authorized")
    try:
        pairing_session = bootstrap_pairing_session_config()
    except DeviceEnrollmentError as exc:
        raise HTTPException(503, "bootstrap pairing session is unavailable") from exc
    supplied_session = request.headers.get("x-pairing-session-id", "").strip()
    if not supplied_session:
        raise HTTPException(503, "bootstrap pairing session is required")
    if not hmac.compare_digest(supplied_session, pairing_session.session_id):
        raise HTTPException(401, "bootstrap pairing is not authorized")
    return pairing_session


def _authenticated_credential_id(request: Request):
    credential_id = getattr(request.state, "device_credential_id", None)
    if credential_id is None:
        raise HTTPException(401, "active device token is required")
    return credential_id


@router.post("/pair", response_model=IssuedDeviceToken, status_code=201)
def pair(
    payload: PairDeviceRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> IssuedDeviceToken:
    pairing_session = _bootstrap_pairing_authorized(request)
    try:
        result = pair_device(
            db,
            device_id=payload.device_id,
            metadata=payload.metadata,
            idempotency_key=_idempotency_key(request),
            pairing_session=pairing_session,
        )
    except DeviceEnrollmentReplay as exc:
        raise HTTPException(409, "pairing request already completed") from exc
    except DeviceEnrollmentConflict as exc:
        raise HTTPException(
            409,
            "device enrollment changed; retry with a new key",
        ) from exc
    except ValueError as exc:
        # DeviceEnrollmentError is a ValueError subclass.  Keeping this
        # boundary slightly wider also converts legacy validator ValueErrors
        # into the same fail-closed API contract instead of leaking a 500.
        raise HTTPException(422, "device enrollment request is invalid") from exc
    return _response(result, response)


@router.post("/rotate", response_model=IssuedDeviceToken)
def rotate(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> IssuedDeviceToken:
    try:
        result = rotate_device(
            db,
            credential_id=_authenticated_credential_id(request),
            idempotency_key=_idempotency_key(request),
        )
    except DeviceEnrollmentReplay as exc:
        raise HTTPException(409, "rotation request already completed") from exc
    except DeviceEnrollmentConflict as exc:
        raise HTTPException(409, "device credential is no longer active") from exc
    except DeviceEnrollmentError as exc:
        raise HTTPException(422, "device rotation request is invalid") from exc
    return _response(result, response)


@router.post("/revoke", response_model=RevocationOutcome)
def revoke(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> RevocationOutcome:
    bearer = getattr(request.state, "device_revocation_bearer", None)
    if not isinstance(bearer, str):
        raise HTTPException(401, "active device token is required")
    try:
        revoked = revoke_device_token(db, token=bearer)
    except DeviceEnrollmentConflict as exc:
        raise HTTPException(409, "device credential is no longer active") from exc
    except DeviceEnrollmentError as exc:
        raise HTTPException(401, "active device token is required") from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return RevocationOutcome(status="revoked" if revoked else "already_revoked")


@router.post("/revoke-lost", response_model=RevocationOutcome)
def revoke_lost(
    payload: LostDeviceRevokeRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> Response | RevocationOutcome:
    try:
        revoked = revoke_lost_device(
            db,
            actor_credential_id=_authenticated_credential_id(request),
            target_device_id=payload.device_id,
            target_generation=payload.generation,
        )
    except DeviceEnrollmentConflict as exc:
        raise HTTPException(409, "lost device revocation is not available") from exc
    except DeviceEnrollmentError as exc:
        raise HTTPException(422, "lost device revocation request is invalid") from exc
    if revoked:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return RevocationOutcome(status="already_revoked")
