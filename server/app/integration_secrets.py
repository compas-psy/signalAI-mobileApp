"""Server-side vault for broker credentials entered from the personal app.

The mobile client may *set* credentials but can never read them back. Values
are encrypted in PostgreSQL with pgcrypto and only status metadata leaves the
server. This keeps server-owned trading credentials out of the APK.

T-Invest Sandbox execution is server-owned too: the phone may migrate its
legacy sandbox bearer into the write-only ``tinvest_sandbox_trade`` slot, then
all provider I/O originates from the VPS.  The sandbox slot is deliberately
separate from ``tinvest_trade`` and can never be used for live execution.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .db import database_url
from .execution.kill_switch import execution_control_lock


class IntegrationSecretStoreError(RuntimeError):
    """Stable secret-free failure raised by the encrypted credential store."""


_SECRET_STORE_ERROR = "integration secret store unavailable"


@dataclass(frozen=True)
class IntegrationSpec:
    slot: str
    venue: str
    title: str
    purpose: str
    environment: str
    fields: tuple[str, ...]
    required: bool = True


_LIGHTER_FIELDS = ("account_index", "api_key_index", "api_private_key")
_LIGHTER_SLOTS = {
    "lighter_read",
    "lighter_testnet_trade",
    "lighter_trade",
}
_LIGHTER_LIVE_TRADE_SLOT = "lighter_trade"
_LIGHTER_LIVE_VAULT_ENV = "SIGNALAI_LIGHTER_LIVE_SECRETS_KEY"


SPECS: tuple[IntegrationSpec, ...] = (
    IntegrationSpec(
        "tinvest_invest_read", "TINVEST", "Инвестиции · чтение",
        "портфель, позиции и метаданные; заявки запрещены", "live", ("token",),
    ),
    IntegrationSpec(
        "tinvest_sandbox_trade", "TINVEST", "Инвестиции · Sandbox",
        "server-side T-Invest Sandbox; только виртуальные средства",
        "sandbox", ("token",), required=False,
    ),
    IntegrationSpec(
        "tinvest_trade", "TINVEST", "Идеи · торговля",
        "торговый счёт; live-исполнение остаётся закрыто отдельным gate", "live", ("token",),
    ),
    IntegrationSpec(
        "bybit_read", "BYBIT", "Live · чтение",
        "баланс и приватные данные счёта; без права Trade/Withdraw", "live",
        ("api_key", "api_secret"),
    ),
    IntegrationSpec(
        "bybit_testnet_trade", "BYBIT", "Testnet · торговля",
        "необязательный внешний testnet; server-side paper на реальных котировках работает без него",
        "testnet", ("api_key", "api_secret"), required=False,
    ),
    IntegrationSpec(
        "bybit_trade", "BYBIT", "Live · торговля",
        "исполнение подтверждённых крипто-сделок; Withdraw запрещён", "live",
        ("api_key", "api_secret"),
    ),
    IntegrationSpec(
        "lighter_read", "LIGHTER", "Lighter · чтение",
        "приватные данные счёта; торговые действия этим слотом не выполняются",
        "live", _LIGHTER_FIELDS,
    ),
    IntegrationSpec(
        "lighter_testnet_trade", "LIGHTER", "Lighter · Testnet",
        "необязательная testnet-подпись для будущей проверки execution path",
        "testnet", _LIGHTER_FIELDS, required=False,
    ),
    IntegrationSpec(
        "lighter_trade", "LIGHTER", "Lighter · торговля",
        "server-side API-key signing material; LIVE остаётся закрыт отдельным gate",
        "live", _LIGHTER_FIELDS,
    ),
)

BY_SLOT = {spec.slot: spec for spec in SPECS}


def _generic_encryption_key() -> str:
    explicit = os.environ.get("SIGNALAI_SECRETS_KEY", "").strip()
    if explicit:
        if len(explicit) < 32:
            raise RuntimeError("SIGNALAI_SECRETS_KEY must contain >=32 characters")
        material = explicit
    else:
        password = make_url(database_url()).password or ""
        if not password:
            raise RuntimeError("database password is required for secret-store encryption")
        material = password
    return hashlib.sha256(f"signalai-broker-v1:{material}".encode()).hexdigest()


def _lighter_live_encryption_key() -> str:
    """Return the isolated key for live Lighter signing material only.

    A database password or the generic integration-vault key is intentionally
    never accepted here. Compromise/reuse of either must not decrypt the private
    key capable of signing mainnet Lighter actions.
    """

    material = os.environ.get(_LIGHTER_LIVE_VAULT_ENV, "").strip()
    if not material:
        raise RuntimeError(f"{_LIGHTER_LIVE_VAULT_ENV} is required for Lighter live signing secrets")
    if len(material) < 32:
        raise RuntimeError(f"{_LIGHTER_LIVE_VAULT_ENV} must contain >=32 characters")
    return hashlib.sha256(f"signalai-lighter-live-v1:{material}".encode()).hexdigest()


def _encryption_key(slot: str) -> str:
    if slot == _LIGHTER_LIVE_TRADE_SLOT:
        return _lighter_live_encryption_key()
    return _generic_encryption_key()


def ensure_store(db: Session) -> None:
    db.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS signalai_integration_secrets (
            slot TEXT PRIMARY KEY,
            encrypted_payload BYTEA NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))


def _parse_lighter_index(field: str, value: str, *, maximum: int | None = None) -> int:
    if not value.isascii() or not value.isdigit():
        raise ValueError(f"поле {field} должно быть целым неотрицательным числом")
    parsed = int(value)
    if maximum is not None and parsed > maximum:
        raise ValueError(f"поле {field} должно быть в диапазоне 0..{maximum}")
    return parsed


def _validate_lighter_values(values: dict[str, str]) -> None:
    _parse_lighter_index("account_index", values["account_index"])
    _parse_lighter_index("api_key_index", values["api_key_index"], maximum=253)

    private_key = values["api_private_key"]
    encoded = private_key[2:] if private_key.lower().startswith("0x") else private_key
    if (
        not encoded
        or len(encoded) % 2 != 0
        or re.fullmatch(r"[0-9a-fA-F]+", encoded) is None
    ):
        raise ValueError(
            "поле api_private_key должно быть непустым hex-encoded API private key"
        )


def validate_values(spec: IntegrationSpec, values: dict[str, str]) -> dict[str, str]:
    if set(values) != set(spec.fields):
        expected = ", ".join(spec.fields)
        raise ValueError(f"ожидаются ровно поля: {expected}")
    cleaned: dict[str, str] = {}
    for field in spec.fields:
        value = values[field].strip()
        if not value:
            raise ValueError(f"поле {field} пустое")
        if "\n" in value or "\r" in value or "\x00" in value:
            raise ValueError(f"поле {field} должно быть одной строкой")
        if len(value) > 8192:
            raise ValueError(f"поле {field} слишком длинное")
        cleaned[field] = value
    if spec.slot in _LIGHTER_SLOTS:
        _validate_lighter_values(cleaned)
    return cleaned


def _save_secret_unlocked(
    db: Session,
    spec: IntegrationSpec,
    cleaned: dict[str, str],
    *,
    actor: str,
) -> datetime:
    """Persist one already-validated secret; caller owns any required lock.

    ``clock_timestamp()`` is intentional here. PostgreSQL ``now()`` is frozen
    at transaction start, so two credential rotations inside one long-lived
    transaction can otherwise receive the same generation marker. Sandbox
    readiness and live credential governance must invalidate immediately on
    every actual write, not merely on every transaction.
    """

    existed = False
    if spec.slot == _LIGHTER_LIVE_TRADE_SLOT:
        existed = (
            db.execute(
                text("SELECT 1 FROM signalai_integration_secrets WHERE slot = :slot"),
                {"slot": spec.slot},
            ).first()
            is not None
        )

    payload = json.dumps(cleaned, ensure_ascii=False, sort_keys=True)
    secret_key = _encryption_key(spec.slot)
    row = None
    store_failed = False
    try:
        row = db.execute(text("""
            INSERT INTO signalai_integration_secrets(slot, encrypted_payload, updated_at)
            VALUES (:slot, pgp_sym_encrypt(:payload, :secret_key, 'cipher-algo=aes256'), clock_timestamp())
            ON CONFLICT (slot) DO UPDATE SET
                encrypted_payload = EXCLUDED.encrypted_payload,
                updated_at = clock_timestamp()
            RETURNING updated_at
        """), {"slot": spec.slot, "payload": payload, "secret_key": secret_key}).one()
    except SQLAlchemyError:
        # Never let SQLAlchemy's StatementError carry bound secret parameters
        # into an API traceback or log record. Raise only after leaving the
        # exception handler so the sanitized exception has no secret-bearing
        # __context__ chain either.
        store_failed = True
    if store_failed:
        raise IntegrationSecretStoreError(_SECRET_STORE_ERROR)
    assert row is not None

    if spec.slot == _LIGHTER_LIVE_TRADE_SLOT:
        # Lazy import keeps the generic vault independent from execution model
        # initialization while preserving a single transaction boundary.
        from .execution.canary_policy import record_lighter_trade_generation

        record_lighter_trade_generation(
            db,
            action="ROTATED" if existed else "CREATED",
            actor=actor,
            account_index=int(cleaned["account_index"]),
            api_key_index=int(cleaned["api_key_index"]),
        )
    return row[0]


def save_secret(
    db: Session,
    spec: IntegrationSpec,
    values: dict[str, str],
    *,
    actor: str = "server_internal",
) -> datetime:
    """Store one encrypted secret and rotate live Lighter generation atomically.

    Only the live Lighter trade slot shares the session-level execution lock
    with the Canary submit window. This lock survives the submit-side durability
    commit, unlike transaction row locks. Unrelated integration writes retain
    their previous independent behavior.
    """

    ensure_store(db)
    cleaned = validate_values(spec, values)
    if spec.slot != _LIGHTER_LIVE_TRADE_SLOT:
        return _save_secret_unlocked(db, spec, cleaned, actor=actor)
    with execution_control_lock(db):
        return _save_secret_unlocked(db, spec, cleaned, actor=actor)


def _delete_secret_unlocked(
    db: Session,
    slot: str,
    *,
    actor: str,
) -> None:
    """Delete one secret; caller owns any required execution serialization."""

    current_generation = None
    if slot == _LIGHTER_LIVE_TRADE_SLOT:
        from .execution.canary_policy import current_lighter_trade_generation

        current_generation = current_lighter_trade_generation(db)

    result = db.execute(
        text("DELETE FROM signalai_integration_secrets WHERE slot = :slot"),
        {"slot": slot},
    )
    if (
        slot == _LIGHTER_LIVE_TRADE_SLOT
        and result.rowcount
        and current_generation is not None
    ):
        from .execution.canary_policy import record_lighter_trade_generation

        record_lighter_trade_generation(
            db,
            action="REVOKED",
            actor=actor,
            account_index=current_generation.account_index,
            api_key_index=current_generation.api_key_index,
        )


def delete_secret(
    db: Session,
    slot: str,
    *,
    actor: str = "server_internal",
) -> None:
    ensure_store(db)
    if slot != _LIGHTER_LIVE_TRADE_SLOT:
        _delete_secret_unlocked(db, slot, actor=actor)
        return
    with execution_control_lock(db):
        _delete_secret_unlocked(db, slot, actor=actor)


def configured_slots(db: Session) -> dict[str, datetime]:
    ensure_store(db)
    rows = db.execute(text("SELECT slot, updated_at FROM signalai_integration_secrets")).all()
    return {str(row[0]): row[1] for row in rows}


def load_secret(db: Session, slot: str) -> dict[str, str] | None:
    """Decrypt a credential for server workers only. Never exposed by HTTP."""
    ensure_store(db)
    secret_key = _encryption_key(slot)
    row = None
    store_failed = False
    try:
        row = db.execute(text("""
            SELECT pgp_sym_decrypt(encrypted_payload, :secret_key)
            FROM signalai_integration_secrets
            WHERE slot = :slot
        """), {"slot": slot, "secret_key": secret_key}).first()
    except SQLAlchemyError:
        store_failed = True
    if store_failed:
        raise IntegrationSecretStoreError(_SECRET_STORE_ERROR)
    if row is None:
        return None
    decoded = json.loads(row[0])
    return {str(key): str(value) for key, value in decoded.items()}
