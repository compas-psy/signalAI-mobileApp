"""One-time owner pairing and active-device credential lifecycle."""
from __future__ import annotations

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
    pair_device,
    revoke_device_token,
    revoke_lost_device,
    rotate_device,
)
from ...device_pairing import authenticate_pairing_session
from ...schemas.common import ApiModel

router = APIRouter(prefix="/device-enrollment", tags=["device-enrollment"])


class PairDeviceRequest(ApiModel):
    device_id: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    metadata: dict[str, str] = Field(default_factory=dict)
    owner_public_key_spki_b64: str | None = Field(
        default=None,
        min_length=80,
        max_length=256,
    )


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


def _pairing_authorized(request: Request, db: Session) -> BootstrapPairingSession:
    """Accept only a server-provisioned, short-lived, one-use pairing code."""
    supplied = request.headers.get("x-pairing-session-id", "").strip()
    if not supplied:
        raise HTTPException(401, "pairing code is required")
    pairing_session = authenticate_pairing_session(db, supplied)
    if pairing_session is None:
        raise HTTPException(401, "pairing code is not authorized")
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
    pairing_session = _pairing_authorized(request, db)
    try:
        result = pair_device(
            db,
            device_id=payload.device_id,
            metadata=payload.metadata,
            idempotency_key=_idempotency_key(request),
            pairing_session=pairing_session,
            owner_public_key_spki_b64=payload.owner_public_key_spki_b64,
        )
    except DeviceEnrollmentReplay as exc:
        raise HTTPException(409, "pairing request already completed") from exc
    except DeviceEnrollmentConflict as exc:
        raise HTTPException(
            409,
            "device enrollment changed; retry with a new key",
        ) from exc
    except ValueError as exc:
        # DeviceEnrollmentError is a ValueError subclass. Keeping this
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
