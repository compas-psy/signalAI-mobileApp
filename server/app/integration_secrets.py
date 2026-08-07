"""Server-side vault for broker credentials entered from the personal app.

The mobile client may *set* credentials but can never read them back. Values
are encrypted in PostgreSQL with pgcrypto and only status metadata leaves the
server. This keeps trading credentials out of APK/Android storage while still
letting the owner configure the server from the phone.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from .db import database_url


@dataclass(frozen=True)
class IntegrationSpec:
    slot: str
    venue: str
    title: str
    purpose: str
    environment: str
    fields: tuple[str, ...]


SPECS: tuple[IntegrationSpec, ...] = (
    IntegrationSpec(
        "tinvest_sandbox",
        "TINVEST",
        "Песочница",
        "обкатка исполнения без настоящих денег",
        "sandbox",
        ("token",),
    ),
    IntegrationSpec(
        "tinvest_invest_read",
        "TINVEST",
        "Инвестиции · чтение",
        "портфель, позиции и метаданные; заявки запрещены",
        "live",
        ("token",),
    ),
    IntegrationSpec(
        "tinvest_trade",
        "TINVEST",
        "Идеи · торговля",
        "торговый счёт; live-исполнение остаётся закрыто отдельным gate",
        "live",
        ("token",),
    ),
    IntegrationSpec(
        "bybit_read",
        "BYBIT",
        "Live · чтение",
        "баланс и приватные данные счёта; без права Trade/Withdraw",
        "live",
        ("api_key", "api_secret"),
    ),
    IntegrationSpec(
        "bybit_testnet_trade",
        "BYBIT",
        "Testnet · торговля",
        "обкатка крипто-исполнения без настоящих денег",
        "testnet",
        ("api_key", "api_secret"),
    ),
    IntegrationSpec(
        "bybit_trade",
        "BYBIT",
        "Live · торговля",
        "исполнение подтверждённых крипто-сделок; Withdraw запрещён",
        "live",
        ("api_key", "api_secret"),
    ),
)

BY_SLOT = {spec.slot: spec for spec in SPECS}


def _encryption_key() -> str:
    """Stable server-only encryption key.

    Production may provide SIGNALAI_SECRETS_KEY explicitly. Existing installs
    can upgrade without a new secret: the stable PostgreSQL password is used
    only as input to a domain-separated SHA-256 KDF. Device-token rotation
    therefore does not make broker credentials unreadable.
    """

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


def ensure_store(db: Session) -> None:
    # The official postgres image includes pgcrypto. The role created by
    # POSTGRES_USER owns this database and may enable the extension.
    db.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS signalai_integration_secrets (
                slot TEXT PRIMARY KEY,
                encrypted_payload BYTEA NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
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
    return cleaned


def save_secret(db: Session, spec: IntegrationSpec, values: dict[str, str]) -> datetime:
    ensure_store(db)
    payload = json.dumps(validate_values(spec, values), ensure_ascii=False, sort_keys=True)
    row = db.execute(
        text(
            """
            INSERT INTO signalai_integration_secrets(slot, encrypted_payload, updated_at)
            VALUES (
                :slot,
                pgp_sym_encrypt(:payload, :secret_key, 'cipher-algo=aes256'),
                now()
            )
            ON CONFLICT (slot) DO UPDATE SET
                encrypted_payload = EXCLUDED.encrypted_payload,
                updated_at = now()
            RETURNING updated_at
            """
        ),
        {"slot": spec.slot, "payload": payload, "secret_key": _encryption_key()},
    ).one()
    return row[0]


def delete_secret(db: Session, slot: str) -> None:
    ensure_store(db)
    db.execute(
        text("DELETE FROM signalai_integration_secrets WHERE slot = :slot"),
        {"slot": slot},
    )


def configured_slots(db: Session) -> dict[str, datetime]:
    ensure_store(db)
    rows = db.execute(
        text("SELECT slot, updated_at FROM signalai_integration_secrets")
    ).all()
    return {str(row[0]): row[1] for row in rows}


def load_secret(db: Session, slot: str) -> dict[str, str] | None:
    """Decrypt a credential for server workers only.

    This function is intentionally not exposed by an HTTP endpoint.
    """

    ensure_store(db)
    row = db.execute(
        text(
            """
            SELECT pgp_sym_decrypt(encrypted_payload, :secret_key)
            FROM signalai_integration_secrets
            WHERE slot = :slot
            """
        ),
        {"slot": slot, "secret_key": _encryption_key()},
    ).first()
    if row is None:
        return None
    decoded = json.loads(row[0])
    return {str(key): str(value) for key, value in decoded.items()}
