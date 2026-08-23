"""Release- and credential-bound T-Invest Sandbox round-trip readiness."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ...integration_secrets import configured_slots
from ...models import TInvestSandboxRoundTripProof
from .tinvest import TInvestProviderError

_SOURCE_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SANDBOX_SLOT = "tinvest_sandbox_trade"
_FILL_STATUS = "EXECUTION_REPORT_STATUS_FILL"


@dataclass(frozen=True, slots=True)
class TInvestSandboxContext:
    source_sha: str
    credential_updated_at: datetime


@dataclass(frozen=True, slots=True)
class TInvestSandboxReadiness:
    ready: bool
    proof_id: str | None
    notes: tuple[str, ...]


def _source_sha() -> str | None:
    value = os.environ.get("SIGNALAI_SOURCE_SHA", "").strip().lower()
    if _SOURCE_SHA_RE.fullmatch(value) is None:
        return None
    return value


def _credential_updated_at(db: Session) -> datetime | None:
    return configured_slots(db).get(_SANDBOX_SLOT)


def current_tinvest_sandbox_context(db: Session) -> TInvestSandboxContext:
    source_sha = _source_sha()
    if source_sha is None:
        raise TInvestProviderError(
            code="SERVER_PROVENANCE_MISSING",
            message="server release provenance is unavailable",
        )
    credential_updated_at = _credential_updated_at(db)
    if credential_updated_at is None:
        raise TInvestProviderError(
            code="CREDENTIAL_MISSING",
            message="server sandbox credential is not configured",
        )
    return TInvestSandboxContext(
        source_sha=source_sha,
        credential_updated_at=credential_updated_at,
    )


def scoped_sandbox_diagnostic_key(
    client_key: str,
    context: TInvestSandboxContext,
) -> str:
    """Stable per release+credential identity without exposing either as an order id."""

    material = (
        f"{client_key}|{context.source_sha}|"
        f"{context.credential_updated_at.isoformat()}"
    ).encode()
    digest = hashlib.sha256(material).hexdigest()
    return f"roundtrip-{digest}"


def record_tinvest_sandbox_roundtrip_proof(
    db: Session,
    *,
    context: TInvestSandboxContext,
    symbol: str,
    account_suffix: str,
    buy_order_id: str,
    buy_status: str,
    buy_executed_lots: int,
    sell_order_id: str,
    sell_status: str,
    sell_executed_lots: int,
    position_flat: bool,
) -> str:
    if (
        buy_status != _FILL_STATUS
        or sell_status != _FILL_STATUS
        or buy_executed_lots <= 0
        or sell_executed_lots != buy_executed_lots
        or not position_flat
    ):
        raise ValueError("only a complete provider-FILL flat sandbox round trip may become readiness proof")

    statement = (
        insert(TInvestSandboxRoundTripProof)
        .values(
            source_sha=context.source_sha,
            credential_updated_at=context.credential_updated_at,
            symbol=symbol,
            account_suffix=account_suffix,
            buy_order_id=buy_order_id,
            buy_status=buy_status,
            buy_executed_lots=buy_executed_lots,
            sell_order_id=sell_order_id,
            sell_status=sell_status,
            sell_executed_lots=sell_executed_lots,
            position_flat=True,
        )
        .on_conflict_do_nothing(
            index_elements=["source_sha", "credential_updated_at"]
        )
        .returning(TInvestSandboxRoundTripProof.id)
    )
    inserted = db.execute(statement).scalar_one_or_none()
    if inserted is not None:
        return str(inserted)

    existing = db.execute(
        select(TInvestSandboxRoundTripProof.id).where(
            TInvestSandboxRoundTripProof.source_sha == context.source_sha,
            TInvestSandboxRoundTripProof.credential_updated_at
            == context.credential_updated_at,
        )
    ).scalar_one()
    return str(existing)


def current_tinvest_sandbox_readiness(db: Session) -> TInvestSandboxReadiness:
    source_sha = _source_sha()
    if source_sha is None:
        return TInvestSandboxReadiness(
            ready=False,
            proof_id=None,
            notes=("server release provenance is unavailable",),
        )
    credential_updated_at = _credential_updated_at(db)
    if credential_updated_at is None:
        return TInvestSandboxReadiness(
            ready=False,
            proof_id=None,
            notes=("T-Invest Sandbox credential is not configured on server",),
        )

    exact = db.execute(
        select(TInvestSandboxRoundTripProof.id).where(
            TInvestSandboxRoundTripProof.source_sha == source_sha,
            TInvestSandboxRoundTripProof.credential_updated_at == credential_updated_at,
        )
    ).scalar_one_or_none()
    if exact is not None:
        return TInvestSandboxReadiness(
            ready=True,
            proof_id=str(exact),
            notes=("provider-confirmed sandbox BUY/SELL round trip is current",),
        )

    same_source = db.execute(
        select(TInvestSandboxRoundTripProof.id)
        .where(TInvestSandboxRoundTripProof.source_sha == source_sha)
        .limit(1)
    ).scalar_one_or_none()
    if same_source is not None:
        note = "current sandbox credential has no provider-confirmed round trip"
    else:
        same_credential = db.execute(
            select(TInvestSandboxRoundTripProof.id)
            .where(
                TInvestSandboxRoundTripProof.credential_updated_at
                == credential_updated_at
            )
            .limit(1)
        ).scalar_one_or_none()
        note = (
            "current release has no provider-confirmed sandbox round trip"
            if same_credential is not None
            else "current release/credential has no provider-confirmed sandbox round trip"
        )

    return TInvestSandboxReadiness(ready=False, proof_id=None, notes=(note,))


__all__ = [
    "TInvestSandboxContext",
    "TInvestSandboxReadiness",
    "current_tinvest_sandbox_context",
    "current_tinvest_sandbox_readiness",
    "record_tinvest_sandbox_roundtrip_proof",
    "scoped_sandbox_diagnostic_key",
]
