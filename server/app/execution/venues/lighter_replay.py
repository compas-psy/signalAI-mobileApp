"""Restart-safe Lighter order identity and nonce ownership for SAI-069.

SignalAI keeps its provider-neutral execution identity authoritative. This
module deterministically maps that identity to Lighter's numeric order index
and reserves explicit transaction nonces in PostgreSQL. Provider transport and
signing are intentionally outside this boundary.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ...models.lighter_execution import LighterNonceReservation, LighterOrderIdentity

_INT64_MAX = (1 << 63) - 1


class LighterReplayError(RuntimeError):
    """Base fail-closed error for Lighter replay identity state."""


class LighterNonceBusy(LighterReplayError):
    """Another unresolved transaction owns the nonce lane."""


class LighterNonceStateMismatch(LighterReplayError):
    """Provider nonce evidence conflicts with durable local evidence."""


def _validate_non_negative_int64(field: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LighterReplayError(f"{field} must be an integer")
    if value < 0 or value > _INT64_MAX:
        raise LighterReplayError(f"{field} must be in signed int64 range")
    return value


def _advisory_key(namespace: str, identity: str) -> int:
    digest = hashlib.sha256(f"{namespace}:{identity}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], byteorder="big", signed=False) & _INT64_MAX
    return value or 1


def _lock(db: Session, namespace: str, identity: str) -> None:
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _advisory_key(namespace, identity)},
    )


def derive_lighter_client_order_index(client_order_id: str) -> int:
    """Map SignalAI's stable order id to Lighter's positive signed int64 domain."""
    if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 96:
        raise LighterReplayError("client_order_id must be a non-empty string up to 96 chars")
    digest = hashlib.sha256(f"lighter-order-v1:{client_order_id}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], byteorder="big", signed=False) & _INT64_MAX
    return value or 1


def resolve_lighter_order_identity(
    db: Session,
    *,
    account_index: int,
    client_order_id: str,
) -> LighterOrderIdentity:
    account_index = _validate_non_negative_int64("account_index", account_index)
    if not isinstance(client_order_id, str) or not client_order_id or len(client_order_id) > 96:
        raise LighterReplayError("client_order_id must be a non-empty string up to 96 chars")

    _lock(db, "lighter-client-order-id", client_order_id)
    existing = db.execute(
        select(LighterOrderIdentity).where(
            LighterOrderIdentity.client_order_id == client_order_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.account_index != account_index:
            raise LighterReplayError(
                "client_order_id is already bound to a different Lighter account"
            )
        return existing

    client_order_index = derive_lighter_client_order_index(client_order_id)
    _lock(
        db,
        "lighter-client-order-index",
        f"{account_index}:{client_order_index}",
    )
    collision = db.execute(
        select(LighterOrderIdentity).where(
            LighterOrderIdentity.account_index == account_index,
            LighterOrderIdentity.client_order_index == client_order_index,
        )
    ).scalar_one_or_none()
    if collision is not None:
        raise LighterReplayError(
            "Lighter client_order_index collision with another SignalAI order"
        )

    identity = LighterOrderIdentity(
        account_index=account_index,
        client_order_id=client_order_id,
        client_order_index=client_order_index,
    )
    db.add(identity)
    db.flush()
    return identity


def _validate_nonce_scope(
    *,
    account_index: int,
    api_key_index: int,
    replay_key: str,
    provider_next_nonce: int | None = None,
) -> tuple[int, int, str, int | None]:
    account_index = _validate_non_negative_int64("account_index", account_index)
    if isinstance(api_key_index, bool) or not isinstance(api_key_index, int):
        raise LighterReplayError("api_key_index must be an integer")
    if api_key_index < 0 or api_key_index > 253:
        raise LighterReplayError("api_key_index must be in range 0..253")
    if not isinstance(replay_key, str) or not replay_key or len(replay_key) > 192:
        raise LighterReplayError("replay_key must be a non-empty string up to 192 chars")
    if provider_next_nonce is not None:
        provider_next_nonce = _validate_non_negative_int64(
            "provider_next_nonce", provider_next_nonce
        )
    return account_index, api_key_index, replay_key, provider_next_nonce


def reserve_lighter_nonce(
    db: Session,
    *,
    account_index: int,
    api_key_index: int,
    replay_key: str,
    provider_next_nonce: int,
) -> LighterNonceReservation:
    account_index, api_key_index, replay_key, parsed_provider_nonce = _validate_nonce_scope(
        account_index=account_index,
        api_key_index=api_key_index,
        replay_key=replay_key,
        provider_next_nonce=provider_next_nonce,
    )
    assert parsed_provider_nonce is not None

    # Stable lock order prevents same-replay and same-lane races across workers.
    _lock(db, "lighter-replay-key", replay_key)
    _lock(db, "lighter-nonce-scope", f"{account_index}:{api_key_index}")

    existing = db.execute(
        select(LighterNonceReservation).where(
            LighterNonceReservation.replay_key == replay_key
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.account_index != account_index
            or existing.api_key_index != api_key_index
        ):
            raise LighterReplayError(
                "replay_key is already bound to a different Lighter nonce scope"
            )
        return existing

    active = db.execute(
        select(LighterNonceReservation).where(
            LighterNonceReservation.account_index == account_index,
            LighterNonceReservation.api_key_index == api_key_index,
            LighterNonceReservation.state == "RESERVED",
        )
    ).scalar_one_or_none()
    if active is not None:
        raise LighterNonceBusy(
            f"nonce lane is busy with unresolved replay key {active.replay_key}"
        )

    highest_consumed = db.scalar(
        select(func.max(LighterNonceReservation.nonce)).where(
            LighterNonceReservation.account_index == account_index,
            LighterNonceReservation.api_key_index == api_key_index,
            LighterNonceReservation.state == "CONSUMED",
        )
    )
    if highest_consumed is not None and parsed_provider_nonce <= int(highest_consumed):
        raise LighterNonceStateMismatch(
            "provider_next_nonce did not advance beyond consumed local evidence"
        )

    reservation = LighterNonceReservation(
        account_index=account_index,
        api_key_index=api_key_index,
        replay_key=replay_key,
        nonce=parsed_provider_nonce,
        state="RESERVED",
        consumed_at=None,
    )
    db.add(reservation)
    db.flush()
    return reservation


def mark_lighter_nonce_consumed(
    db: Session,
    *,
    replay_key: str,
    consumed_at: datetime,
) -> LighterNonceReservation:
    if not isinstance(replay_key, str) or not replay_key or len(replay_key) > 192:
        raise LighterReplayError("replay_key must be a non-empty string up to 192 chars")
    if consumed_at.tzinfo is None or consumed_at.utcoffset() is None:
        raise LighterReplayError("consumed_at must be timezone-aware")

    _lock(db, "lighter-replay-key", replay_key)
    reservation = db.execute(
        select(LighterNonceReservation).where(
            LighterNonceReservation.replay_key == replay_key
        )
    ).scalar_one_or_none()
    if reservation is None:
        raise LighterReplayError("unknown Lighter replay_key")
    if reservation.state == "CONSUMED":
        return reservation
    if reservation.state != "RESERVED":
        raise LighterNonceStateMismatch(
            f"unexpected Lighter nonce reservation state {reservation.state!r}"
        )

    _lock(
        db,
        "lighter-nonce-scope",
        f"{reservation.account_index}:{reservation.api_key_index}",
    )
    reservation.state = "CONSUMED"
    reservation.consumed_at = consumed_at
    db.flush()
    return reservation


__all__ = [
    "LighterNonceBusy",
    "LighterNonceStateMismatch",
    "LighterReplayError",
    "derive_lighter_client_order_index",
    "mark_lighter_nonce_consumed",
    "reserve_lighter_nonce",
    "resolve_lighter_order_identity",
]
