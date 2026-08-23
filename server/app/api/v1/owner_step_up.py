"""Non-authorizing owner-presence self-test endpoints.

These routes prove that the currently authenticated device can produce a fresh
signature from its separately enrolled owner key.  They deliberately expose no
arbitrary purpose/payload input and have no execution-mode, venue, capital, or
promotion side effects.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy.orm import Session

from ...db import get_db
from ...owner_step_up import (
    IssuedOwnerStepUpChallenge,
    OwnerStepUpError,
    OwnerStepUpProofReceipt,
    issue_owner_step_up_challenge,
    verify_owner_step_up_signature,
)
from ...schemas.common import ApiModel

router = APIRouter(prefix="/owner-step-up", tags=["owner-step-up"])

_SELF_TEST_PURPOSE = "OWNER_STEP_UP_SELF_TEST"
_SELF_TEST_TTL = timedelta(seconds=60)


class OwnerStepUpChallengeResponse(ApiModel):
    challenge_id: uuid.UUID
    device_id: str
    owner_key_fingerprint: str
    purpose: str
    payload_hash: str
    message: str
    expires_at: datetime


class OwnerStepUpVerifyRequest(ApiModel):
    challenge_id: uuid.UUID
    signature_b64: str = Field(min_length=8, max_length=256)


class OwnerStepUpReceiptResponse(ApiModel):
    challenge_id: uuid.UUID
    device_id: str
    owner_key_fingerprint: str
    purpose: str
    payload_hash: str
    verified_at: datetime


def _authenticated_identity(request: Request) -> tuple[object, str, int]:
    credential_id = getattr(request.state, "device_credential_id", None)
    device_id = getattr(request.state, "device_id", None)
    generation = getattr(request.state, "device_generation", None)
    if (
        credential_id is None
        or not isinstance(device_id, str)
        or not device_id
        or not isinstance(generation, int)
        or generation < 1
    ):
        raise HTTPException(401, "active device token is required")
    return credential_id, device_id, generation


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _challenge_response(
    value: IssuedOwnerStepUpChallenge,
) -> OwnerStepUpChallengeResponse:
    return OwnerStepUpChallengeResponse(
        challenge_id=value.challenge_id,
        device_id=value.device_id,
        owner_key_fingerprint=value.owner_key_fingerprint,
        purpose=value.purpose,
        payload_hash=value.payload_hash,
        message=value.message,
        expires_at=value.expires_at,
    )


def _receipt_response(value: OwnerStepUpProofReceipt) -> OwnerStepUpReceiptResponse:
    return OwnerStepUpReceiptResponse(
        challenge_id=value.challenge_id,
        device_id=value.device_id,
        owner_key_fingerprint=value.owner_key_fingerprint,
        purpose=value.purpose,
        payload_hash=value.payload_hash,
        verified_at=value.verified_at,
    )


@router.post(
    "/self-test/challenge",
    response_model=OwnerStepUpChallengeResponse,
    status_code=201,
)
def issue_self_test_challenge(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> OwnerStepUpChallengeResponse:
    credential_id, device_id, generation = _authenticated_identity(request)
    try:
        challenge = issue_owner_step_up_challenge(
            db,
            credential_id=credential_id,
            purpose=_SELF_TEST_PURPOSE,
            payload={
                "device_generation": generation,
                "device_id": device_id,
            },
            ttl=_SELF_TEST_TTL,
        )
    except OwnerStepUpError as exc:
        raise HTTPException(403, "owner step-up proof rejected") from exc
    _no_store(response)
    return _challenge_response(challenge)


@router.post(
    "/self-test/verify",
    response_model=OwnerStepUpReceiptResponse,
)
def verify_self_test_challenge(
    payload: OwnerStepUpVerifyRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> OwnerStepUpReceiptResponse:
    credential_id, _device_id, _generation = _authenticated_identity(request)
    try:
        receipt = verify_owner_step_up_signature(
            db,
            credential_id=credential_id,
            challenge_id=payload.challenge_id,
            signature_b64=payload.signature_b64,
        )
    except OwnerStepUpError as exc:
        raise HTTPException(403, "owner step-up proof rejected") from exc
    _no_store(response)
    return _receipt_response(receipt)


__all__ = ["router"]
