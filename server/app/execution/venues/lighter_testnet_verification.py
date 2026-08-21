"""Fail-closed Lighter testnet admission and create/cancel smoke path (SAI-075).

The module deliberately does not construct a provider SDK client or load secrets.
Callers must inject one transport bound to the already-loaded testnet trade
credentials. Admission is pinned to Lighter testnet and to the exact transport
scope later used by the smoke action.

The smoke action reuses SAI-069/070 durable identity/nonce handling. It submits
only a post-only limit followed by cancellation and never promotes LIVE.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from ...experiments.venue_shadow_scorecard_v1 import VenueShadowScorecardResult
from .lighter import LighterEnvironment, lighter_base_url
from .lighter_actions import LighterActionTransport, LighterOrderActions, SessionFactory
from .lighter_auth import LighterServerCredentials
from .lighter_facts import LighterMarketFact

_LIGHTER_TESTNET_BASE_URL = lighter_base_url(LighterEnvironment.TESTNET)
_LIGHTER_TESTNET_CHAIN_ID = 300
_MAX_ACCOUNT_INDEX = (1 << 63) - 1
_MAX_API_KEY_INDEX = 253


class LighterTestnetAdmissionStatus(StrEnum):
    BLOCKED = "BLOCKED"
    READY = "READY"


class LighterTestnetAdmissionError(RuntimeError):
    """Raised when a smoke action attempts to bypass verified admission."""


class LighterTestnetTransport(LighterActionTransport, Protocol):
    base_url: str
    chain_id: int

    def check_client(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class LighterTestnetAdmission:
    status: LighterTestnetAdmissionStatus
    reasons: tuple[str, ...]
    account_index: int | None
    api_key_index: int | None
    base_url: str | None
    chain_id: int | None
    provider_next_nonce: int | None
    observed_at: datetime
    transport_fingerprint: str | None

    @property
    def eligible_for_order_smoke(self) -> bool:
        return self.status is LighterTestnetAdmissionStatus.READY

    @property
    def eligible_for_live(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class LighterTestnetSmokeResult:
    client_order_id: str
    create_tx_hash: str
    cancel_tx_hash: str

    @property
    def eligible_for_live(self) -> bool:
        return False


def _aware(value: datetime) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _valid_int(value: object, *, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= maximum
    )


def _transport_scope(
    transport: LighterTestnetTransport,
) -> tuple[str | None, int | None, int | None, int | None]:
    base_url = getattr(transport, "base_url", None)
    chain_id = getattr(transport, "chain_id", None)
    account_index = getattr(transport, "account_index", None)
    api_key_index = getattr(transport, "api_key_index", None)
    return (
        base_url if isinstance(base_url, str) else None,
        chain_id if _valid_int(chain_id, maximum=_MAX_ACCOUNT_INDEX) else None,
        account_index if _valid_int(account_index, maximum=_MAX_ACCOUNT_INDEX) else None,
        api_key_index if _valid_int(api_key_index, maximum=_MAX_API_KEY_INDEX) else None,
    )


def _transport_fingerprint(
    *,
    base_url: str,
    chain_id: int,
    account_index: int,
    api_key_index: int,
) -> str:
    payload = f"{base_url}|{chain_id}|{account_index}|{api_key_index}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _blocked(
    *,
    reason: str,
    observed_at: datetime,
    account_index: int | None = None,
    api_key_index: int | None = None,
    base_url: str | None = None,
    chain_id: int | None = None,
) -> LighterTestnetAdmission:
    return LighterTestnetAdmission(
        status=LighterTestnetAdmissionStatus.BLOCKED,
        reasons=(reason,),
        account_index=account_index,
        api_key_index=api_key_index,
        base_url=base_url,
        chain_id=chain_id,
        provider_next_nonce=None,
        observed_at=observed_at,
        transport_fingerprint=None,
    )


def verify_lighter_testnet_admission(
    *,
    credentials: LighterServerCredentials,
    shadow_result: VenueShadowScorecardResult,
    transport: LighterTestnetTransport,
    observed_at: datetime,
) -> LighterTestnetAdmission:
    """Verify the non-secret facts required before one controlled testnet smoke.

    Preconditions are ordered deliberately: shadow evidence, credential context,
    endpoint/chain/scope, provider credential check, then provider nonce. A failed
    local precondition therefore performs no provider call.
    """

    if not _aware(observed_at):
        raise ValueError("observed_at must be timezone-aware")
    if not isinstance(credentials, LighterServerCredentials):
        raise ValueError("credentials must be LighterServerCredentials")
    if not isinstance(shadow_result, VenueShadowScorecardResult):
        raise ValueError("shadow_result must be VenueShadowScorecardResult")

    if not shadow_result.eligible_for_testnet:
        return _blocked(reason="SHADOW_GATE_NOT_ELIGIBLE", observed_at=observed_at)

    if credentials.environment != "testnet" or credentials.purpose != "trade":
        return _blocked(
            reason="TESTNET_TRADE_CREDENTIALS_REQUIRED",
            observed_at=observed_at,
            account_index=credentials.account_index,
            api_key_index=credentials.api_key_index,
        )

    base_url, chain_id, account_index, api_key_index = _transport_scope(transport)
    if base_url != _LIGHTER_TESTNET_BASE_URL:
        return _blocked(
            reason="TESTNET_ENDPOINT_MISMATCH",
            observed_at=observed_at,
            account_index=account_index,
            api_key_index=api_key_index,
            base_url=base_url,
            chain_id=chain_id,
        )
    if chain_id != _LIGHTER_TESTNET_CHAIN_ID:
        return _blocked(
            reason="TESTNET_CHAIN_ID_MISMATCH",
            observed_at=observed_at,
            account_index=account_index,
            api_key_index=api_key_index,
            base_url=base_url,
            chain_id=chain_id,
        )
    if (
        account_index != credentials.account_index
        or api_key_index != credentials.api_key_index
    ):
        return _blocked(
            reason="TRANSPORT_CREDENTIAL_SCOPE_MISMATCH",
            observed_at=observed_at,
            account_index=account_index,
            api_key_index=api_key_index,
            base_url=base_url,
            chain_id=chain_id,
        )

    try:
        check_error = transport.check_client()
    except Exception:
        return _blocked(
            reason="PROVIDER_CREDENTIAL_CHECK_FAILED",
            observed_at=observed_at,
            account_index=account_index,
            api_key_index=api_key_index,
            base_url=base_url,
            chain_id=chain_id,
        )
    if check_error is not None:
        return _blocked(
            reason="PROVIDER_CREDENTIAL_CHECK_FAILED",
            observed_at=observed_at,
            account_index=account_index,
            api_key_index=api_key_index,
            base_url=base_url,
            chain_id=chain_id,
        )

    try:
        provider_next_nonce = transport.next_nonce()
    except Exception:
        return _blocked(
            reason="PROVIDER_NONCE_UNAVAILABLE",
            observed_at=observed_at,
            account_index=account_index,
            api_key_index=api_key_index,
            base_url=base_url,
            chain_id=chain_id,
        )
    if not _valid_int(provider_next_nonce, maximum=_MAX_ACCOUNT_INDEX):
        return _blocked(
            reason="PROVIDER_NONCE_INVALID",
            observed_at=observed_at,
            account_index=account_index,
            api_key_index=api_key_index,
            base_url=base_url,
            chain_id=chain_id,
        )

    assert base_url is not None
    assert chain_id is not None
    assert account_index is not None
    assert api_key_index is not None
    return LighterTestnetAdmission(
        status=LighterTestnetAdmissionStatus.READY,
        reasons=("TESTNET_SESSION_VERIFIED",),
        account_index=account_index,
        api_key_index=api_key_index,
        base_url=base_url,
        chain_id=chain_id,
        provider_next_nonce=provider_next_nonce,
        observed_at=observed_at,
        transport_fingerprint=_transport_fingerprint(
            base_url=base_url,
            chain_id=chain_id,
            account_index=account_index,
            api_key_index=api_key_index,
        ),
    )


def _require_same_ready_transport(
    admission: LighterTestnetAdmission,
    transport: LighterTestnetTransport,
) -> None:
    if not isinstance(admission, LighterTestnetAdmission) or not admission.eligible_for_order_smoke:
        raise LighterTestnetAdmissionError("testnet admission is not eligible for order smoke")

    base_url, chain_id, account_index, api_key_index = _transport_scope(transport)
    if (
        base_url is None
        or chain_id is None
        or account_index is None
        or api_key_index is None
    ):
        raise LighterTestnetAdmissionError("testnet transport scope is invalid")
    fingerprint = _transport_fingerprint(
        base_url=base_url,
        chain_id=chain_id,
        account_index=account_index,
        api_key_index=api_key_index,
    )
    if fingerprint != admission.transport_fingerprint:
        raise LighterTestnetAdmissionError(
            "testnet transport does not match the verified admission scope"
        )


def run_lighter_testnet_create_cancel_smoke(
    *,
    admission: LighterTestnetAdmission,
    session_factory: SessionFactory,
    transport: LighterTestnetTransport,
    market: LighterMarketFact,
    client_order_id: str,
    quantity: Decimal,
    price: Decimal,
    is_ask: bool,
) -> LighterTestnetSmokeResult:
    """Submit one post-only testnet limit and cancel it through replay-safe actions."""

    _require_same_ready_transport(admission, transport)
    actions = LighterOrderActions(session_factory=session_factory, transport=transport)
    create_ack = actions.create_limit(
        market=market,
        client_order_id=client_order_id,
        quantity=quantity,
        price=price,
        is_ask=is_ask,
        post_only=True,
    )
    cancel_ack = actions.cancel(market=market, client_order_id=client_order_id)
    return LighterTestnetSmokeResult(
        client_order_id=client_order_id,
        create_tx_hash=create_ack.tx_hash,
        cancel_tx_hash=cancel_ack.tx_hash,
    )


__all__ = [
    "LighterTestnetAdmission",
    "LighterTestnetAdmissionError",
    "LighterTestnetAdmissionStatus",
    "LighterTestnetSmokeResult",
    "LighterTestnetTransport",
    "run_lighter_testnet_create_cancel_smoke",
    "verify_lighter_testnet_admission",
]
