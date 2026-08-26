"""Authenticated owner-presence and Canary-v1 activation endpoints.

Self-test routes prove that the currently authenticated device can produce a
fresh signature from its separately enrolled owner key. Canary routes bind the
same primitive to one exact server-owned immutable policy/runtime context. No
route accepts client-supplied source/config/paper-only evidence.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from ...db import get_db
from ...execution.canary_owner_activation import (
    CanaryOwnerActivationRejected,
    confirm_canary_owner_activation,
    issue_canary_owner_activation_challenge,
)
from ...execution.canary_runtime import current_canary_runtime_context
from ...execution.enums import ExecutionLifecycleMode
from ...owner_step_up import (
    IssuedOwnerStepUpChallenge,
    OwnerStepUpError,
    OwnerStepUpProofReceipt,
    issue_owner_step_up_challenge,
    verify_owner_step_up_signature,
)
from ...schemas.common import ApiModel

# Deliberately no router-level prefix: self-test keeps its historical
# /owner-step-up path while Canary lives under /execution/canary.
router = APIRouter(tags=["owner-step-up"])

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


class CanaryActivationChallengeRequest(ApiModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, extra="forbid")
    snapshot_hash: str = Field(min_length=64, max_length=64)


class CanaryActivationChallengeResponse(ApiModel):
    challenge_id: uuid.UUID
    snapshot_hash: str
    owner_key_fingerprint: str
    purpose: str
    message: str
    expires_at: datetime
    payload: dict[str, object]


class CanaryActivationConfirmRequest(ApiModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, extra="forbid")
    snapshot_hash: str = Field(min_length=64, max_length=64)
    challenge_id: uuid.UUID
    signature_b64: str = Field(min_length=8, max_length=256)


class CanaryActivationResultResponse(ApiModel):
    challenge_id: uuid.UUID
    snapshot_hash: str
    status: str
    mode: ExecutionLifecycleMode
    blockers: list[str]


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


def _canary_rejection(exc: CanaryOwnerActivationRejected) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "message": str(exc),
            "blockers": list(exc.blockers),
        },
    )


@router.post(
    "/owner-step-up/self-test/challenge",
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
    "/owner-step-up/self-test/verify",
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


@router.post(
    "/execution/canary/activation/challenge",
    response_model=CanaryActivationChallengeResponse,
)
def issue_canary_activation_challenge(
    payload: CanaryActivationChallengeRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> CanaryActivationChallengeResponse:
    credential_id, _device_id, _generation = _authenticated_identity(request)
    try:
        challenge = issue_canary_owner_activation_challenge(
            db,
            credential_id=credential_id,
            snapshot_hash=payload.snapshot_hash,
            context_provider=current_canary_runtime_context,
        )
    except CanaryOwnerActivationRejected as exc:
        raise _canary_rejection(exc) from exc
    _no_store(response)
    return CanaryActivationChallengeResponse(
        challenge_id=challenge.challenge_id,
        snapshot_hash=challenge.snapshot_hash,
        owner_key_fingerprint=challenge.owner_key_fingerprint,
        purpose=challenge.purpose,
        message=challenge.message,
        expires_at=challenge.expires_at,
        payload=challenge.payload,
    )


@router.post(
    "/execution/canary/activation/confirm",
    response_model=CanaryActivationResultResponse,
)
def confirm_canary_activation(
    payload: CanaryActivationConfirmRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> CanaryActivationResultResponse:
    credential_id, _device_id, _generation = _authenticated_identity(request)
    try:
        result = confirm_canary_owner_activation(
            db,
            credential_id=credential_id,
            snapshot_hash=payload.snapshot_hash,
            challenge_id=payload.challenge_id,
            signature_b64=payload.signature_b64,
            context_provider=current_canary_runtime_context,
        )
    except CanaryOwnerActivationRejected as exc:
        raise _canary_rejection(exc) from exc
    _no_store(response)
    return CanaryActivationResultResponse(
        challenge_id=result.challenge_id,
        snapshot_hash=result.snapshot_hash,
        status=result.status,
        mode=result.mode,
        blockers=list(result.blockers),
    )


__all__ = [
    "CanaryActivationChallengeRequest",
    "CanaryActivationChallengeResponse",
    "CanaryActivationConfirmRequest",
    "CanaryActivationResultResponse",
    "confirm_canary_owner_activation",
    "current_canary_runtime_context",
    "issue_canary_owner_activation_challenge",
    "router",
]
