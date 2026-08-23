"""Read-only derivation of Lighter mainnet transport identity from durable facts.

This module never loads a private key, constructs a provider client, changes
execution mode, or grants promotion authority.  It only turns an exact verified
Canary snapshot plus the current non-revoked credential generation into the
non-secret scope required by the isolated SDK transport factory.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.canary_policy import CanaryPolicySnapshot
from .canary_policy import (
    CanaryPolicyError,
    current_lighter_trade_generation,
    verify_persisted_canary_snapshot,
)
from .venues.lighter_sdk_transport import LighterMainnetCanaryScope

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class CanaryTransportScopeError(ValueError):
    """The requested DB facts cannot safely authorize mainnet transport identity."""


def resolve_lighter_mainnet_canary_scope(
    db: Session,
    *,
    snapshot_hash: str,
) -> LighterMainnetCanaryScope:
    """Derive one exact non-secret scope from immutable/current DB facts."""

    normalized = str(snapshot_hash).strip().lower()
    if _HEX64.fullmatch(normalized) is None:
        raise CanaryTransportScopeError("snapshot_hash must be a SHA-256 hex digest")

    snapshot = db.execute(
        select(CanaryPolicySnapshot).where(
            CanaryPolicySnapshot.snapshot_hash == normalized
        )
    ).scalar_one_or_none()
    if snapshot is None:
        raise CanaryTransportScopeError("Canary policy snapshot does not exist")

    try:
        payload = verify_persisted_canary_snapshot(snapshot)
    except CanaryPolicyError as exc:
        raise CanaryTransportScopeError(
            "Canary snapshot integrity verification failed"
        ) from exc

    current = current_lighter_trade_generation(db)
    if current is None:
        raise CanaryTransportScopeError(
            "current credential generation is missing or revoked"
        )
    if (
        current.generation_id != payload["credential_generation_id"]
        or current.account_index != payload["account_index"]
        or current.api_key_index != payload["api_key_index"]
    ):
        raise CanaryTransportScopeError(
            "snapshot does not match current credential generation scope"
        )

    return LighterMainnetCanaryScope(
        snapshot_hash=snapshot.snapshot_hash,
        credential_generation_id=current.generation_id,
        account_index=current.account_index,
        api_key_index=current.api_key_index,
    )


__all__ = [
    "CanaryTransportScopeError",
    "resolve_lighter_mainnet_canary_scope",
]
