"""Short-lived verifier-only pairing capabilities for owner devices."""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .device_enrollment import BootstrapPairingSession, DeviceEnrollmentError
from .models.device import DevicePairingSession

_PAIRING_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")


def _now() -> datetime:
    return datetime.now(UTC)


def _verifier(session_id: str) -> str:
    if not isinstance(session_id, str) or _PAIRING_TOKEN_RE.fullmatch(session_id) is None:
        raise DeviceEnrollmentError("pairing session is invalid")
    return hashlib.sha256(
        b"signalai:device-pairing-session:v1\x00" + session_id.encode("utf-8")
    ).hexdigest()


def provision_pairing_session(
    db: Session,
    *,
    ttl: timedelta = timedelta(minutes=15),
) -> tuple[str, datetime]:
    """Create one current one-use pairing secret; persist only its verifier."""
    if ttl <= timedelta(0) or ttl > timedelta(minutes=30):
        raise DeviceEnrollmentError("pairing session ttl is invalid")
    now = _now()
    expires_at = now + ttl

    # A freshly requested code supersedes every previous unused live code.
    # This keeps recovery simple: only the latest Telegram message can pair.
    for row in db.scalars(
        select(DevicePairingSession)
        .where(
            DevicePairingSession.expires_at > now,
            DevicePairingSession.uses < DevicePairingSession.max_uses,
        )
        .with_for_update()
    ):
        row.expires_at = now

    raw = secrets.token_urlsafe(32)
    row = DevicePairingSession(
        session_verifier=_verifier(raw),
        expires_at=expires_at,
        max_uses=1,
        uses=0,
    )
    db.add(row)
    db.flush()
    return raw, expires_at


def authenticate_pairing_session(
    db: Session,
    supplied: str,
    *,
    now: datetime | None = None,
) -> BootstrapPairingSession | None:
    """Resolve a supplied raw code to a live one-use durable capability."""
    try:
        verifier = _verifier(supplied)
    except DeviceEnrollmentError:
        return None
    checked_at = now or _now()
    row = db.scalar(
        select(DevicePairingSession).where(
            DevicePairingSession.session_verifier == verifier
        )
    )
    if row is None or row.expires_at <= checked_at or row.uses >= row.max_uses:
        return None
    return BootstrapPairingSession(
        session_id=supplied,
        verifier=verifier,
        expires_at=row.expires_at,
        max_uses=row.max_uses,
    )


__all__ = ["authenticate_pairing_session", "provision_pairing_session"]
